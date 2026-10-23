from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np
from sklearn.cluster import KMeans

from .settings import Settings
from .corpus import meta_matches, select, verify_meta, write_meta
from .util import read_json, write_json


def import_topics(settings: Settings, corpus: str = "sample") -> dict:
    """Overlay editorial topic prose without changing topic or paper membership IDs."""
    _, corpus_fingerprint = select(settings, corpus)
    path = settings.derived_dir / f"topics-{corpus}.json"
    verify_meta(path, corpus, corpus_fingerprint, "topics")
    import_path = settings.derived_dir / f"topics-import-{corpus}.json"
    if not import_path.exists() and corpus == "sample":
        import_path = settings.root / "data/generated/topics-astra.json"
    overrides = read_json(import_path, {})
    topics = read_json(path, [])
    allowed = {"title", "area", "overview", "whyItMatters", "evolution", "openQuestions", "prerequisites", "difficulty"}
    applied = 0
    for topic in topics:
        change = overrides.get(topic["slug"], {})
        if not isinstance(change, dict):
            continue
        for key in allowed:
            if key in change:
                topic[key] = change[key]
        if change:
            topic["provenance"] = change.get("provenance") or {
                "source": change.get("source", "editorial-import"),
                "model": change.get("model", ""),
            }
            applied += 1
    write_json(path, topics)
    return {"topics": len(topics), "overlays": applied}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "research-topic"


def cluster(settings: Settings, corpus: str = "sample") -> dict:
    """Create a reproducible fine-topic layer from generated embeddings.

    Naming deliberately remains deterministic until the approved summary pass supplies
    editorial topic text. That keeps the pipeline usable without an external model.
    """
    source_papers, corpus_fingerprint = select(settings, corpus)
    enriched_path = settings.derived_dir / f"papers-enriched-{corpus}.json"
    if enriched_path.exists():
        verify_meta(enriched_path, corpus, corpus_fingerprint, "enriched papers")
        papers = read_json(enriched_path, [])
    else:
        papers = source_papers
    vectors_file = settings.derived_dir / f"embeddings-{corpus}.npy"
    if not vectors_file.exists():
        write_json(settings.derived_dir / f"topics-{corpus}.json", [])
        return {"topics": 0, "reason": "embeddings missing", "papers": len(papers)}
    vectors = np.load(vectors_file)
    verify_meta(vectors_file, corpus, corpus_fingerprint, "embeddings")
    if len(vectors) != len(papers):
        raise ValueError("embeddings.npy row count must match selected paper order")
    n_clusters = min(len(papers), max(2, round(math.sqrt(len(papers) / 3))))
    labels = KMeans(n_clusters=n_clusters, random_state=settings.sample_seed, n_init="auto").fit_predict(vectors)
    groups: dict[int, list[dict]] = defaultdict(list)
    for paper, label in zip(papers, labels, strict=True):
        groups[int(label)].append(paper)
    prior_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    prior_rows = read_json(prior_path, []) if meta_matches(prior_path, corpus, corpus_fingerprint) else []
    prior_papers = {paper["slug"]: paper for paper in prior_rows if isinstance(paper, dict) and paper.get("slug")}
    for paper in papers:
        previous = prior_papers.get(paper["slug"], {})
        if previous.get("summary"):
            paper["summary"] = previous["summary"]
    old_path = settings.derived_dir / f"topics-{corpus}.json"
    old_rows = read_json(old_path, []) if meta_matches(old_path, corpus, corpus_fingerprint) else []
    old_topics = {topic["slug"]: topic for topic in old_rows if isinstance(topic, dict) and topic.get("slug")}
    topics: list[dict] = []
    for label, group in sorted(groups.items()):
        representative = sorted(group, key=lambda p: (-(p.get("citationCount") or 0), p["title"]))[0]
        title = representative["title"][:80]
        slug = f"topic-{label:03d}-{_slug(title)[:48]}"
        for paper in group:
            paper["topicSlugs"] = [slug]
            previous = prior_papers.get(paper["slug"], {})
            background = (previous.get("summary") or {}).get("backgroundConcepts", [])
            paper["topicSlugs"] = sorted(set(paper["topicSlugs"] + background))
        topics.append({
            "slug": slug, "title": title, "area": "Machine learning research",
            "overview": f"A cluster of {len(group)} papers related to {title}.",
            "whyItMatters": "Editorial overview pending the approved summary pass.",
            "evolution": "Derived from paper embedding similarity.", "openQuestions": [],
            "prerequisites": [], "difficulty": "intermediate",
            "paperSlugs": [p["slug"] for p in sorted(group, key=lambda p: p["year"])], "relatedTopics": [],
        })
    # Preserve imported editorial prose when deterministic clustering is rerun;
    # membership is always recomputed from the current vectors.
    editable = {"title", "area", "overview", "whyItMatters", "evolution", "openQuestions", "prerequisites", "difficulty", "provenance"}
    for topic in topics:
        previous = old_topics.get(topic["slug"], {})
        if previous.get("provenance"):
            for key in editable:
                if key in previous:
                    topic[key] = previous[key]
    # centroid cosine neighbours
    centers = np.array([vectors[labels == idx].mean(axis=0) for idx in sorted(groups)])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-12)
    sim = centers @ centers.T
    for index, topic in enumerate(topics):
        neighbours = np.argsort(sim[index])[::-1][1:6]
        topic["relatedTopics"] = [topics[int(neighbour)]["slug"] for neighbour in neighbours]
    topics_path = settings.derived_dir / f"topics-{corpus}.json"
    papers_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    write_json(topics_path, topics)
    write_json(papers_path, papers)
    write_meta(topics_path, corpus, corpus_fingerprint)
    write_meta(papers_path, corpus, corpus_fingerprint)
    return {"topics": len(topics), "papers": len(papers)}
