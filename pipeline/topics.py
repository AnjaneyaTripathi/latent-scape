from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans

from .corpus import meta_matches, select, verify_meta, write_meta
from .settings import Settings
from .util import read_json, write_json


def import_topics(settings: Settings, corpus: str = "sample") -> dict:
    """Overlay editorial topic prose without changing topic or paper membership IDs."""
    _, corpus_fingerprint = select(settings, corpus)
    path = settings.derived_dir / f"topics-{corpus}.json"
    verify_meta(path, corpus, corpus_fingerprint, "topics")
    if corpus == "full":
        return _import_full_topics(settings, path, corpus_fingerprint)
    import_path = settings.derived_dir / f"topics-import-{corpus}.json"
    if not import_path.exists() and corpus == "sample":
        import_path = settings.root / "data/generated/topics-astra.json"
    overrides = read_json(import_path, {})
    topics = read_json(path, [])
    allowed = {
        "title",
        "area",
        "overview",
        "whyItMatters",
        "evolution",
        "openQuestions",
        "prerequisites",
        "difficulty",
    }
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


_FULL_REQUIRED = {
    "title",
    "overview",
    "whyItMatters",
    "evolution",
    "openQuestions",
    "prerequisites",
    "difficulty",
    "source",
    "model",
    "corpusFingerprint",
}
_FULL_GUIDE_FIELDS = {
    "title",
    "overview",
    "whyItMatters",
    "evolution",
    "openQuestions",
    "prerequisites",
    "difficulty",
}


def _full_import_paths(settings: Settings) -> tuple[list[Path], Path]:
    generated = settings.root / "data/generated"
    # Topic shards deliberately use this prefix. The broad-area file has its own
    # name, so it cannot be mistaken for a fine-topic guide mapping.
    return sorted(generated.glob("topics-full-*.json")), generated / "broad-areas-full.json"


def _valid_full_guide(value: object, fingerprint: str) -> list[str]:
    if not isinstance(value, dict):
        return ["guide must be an object"]
    missing = sorted(key for key in _FULL_REQUIRED if key not in value)
    errors = [f"missing required field: {key}" for key in missing]
    for field in ("title", "overview", "whyItMatters", "evolution", "source"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            errors.append(f"{field} must be a non-empty string")
    if value.get("model") != "gpt-6-astra":
        errors.append("model must be 'gpt-6-astra'")
    if value.get("corpusFingerprint") != fingerprint:
        errors.append("corpusFingerprint does not match the current full corpus")
    if value.get("difficulty") not in {"beginner", "intermediate", "advanced"}:
        errors.append("difficulty must be beginner, intermediate, or advanced")
    for field in ("openQuestions", "prerequisites"):
        if field in value and (
            not isinstance(value[field], list)
            or not all(isinstance(item, str) for item in value[field])
        ):
            errors.append(f"{field} must be an array of strings")
    return errors


def _load_full_guides(settings: Settings) -> tuple[dict[str, dict], dict[str, list[str]]]:
    guides: dict[str, dict] = {}
    errors: dict[str, list[str]] = {}
    paths, _ = _full_import_paths(settings)
    for import_path in paths:
        rows = read_json(import_path, {})
        if not isinstance(rows, dict):
            errors[str(import_path)] = ["topic shard must be a mapping of topic slug to guide"]
            continue
        for slug, guide in rows.items():
            if not isinstance(slug, str) or not isinstance(guide, dict):
                errors[f"{import_path}:{slug}"] = ["topic slug and guide must be strings/object"]
                continue
            if slug in guides and guides[slug] != guide:
                errors[slug] = ["conflicting guides in full topic shards"]
                guides.pop(slug, None)
                continue
            if slug in errors:
                # A later duplicate cannot rehabilitate a conflict in an earlier
                # shard; authors must resolve it explicitly before import.
                continue
            guides[slug] = guide
    return guides, errors


def _load_full_broad_areas(
    settings: Settings, fingerprint: str
) -> tuple[dict[str, str], dict[str, list[str]]]:
    _, path = _full_import_paths(settings)
    if not path.exists():
        return {}, {"broad-areas-full": ["broad area mapping is missing"]}
    raw = read_json(path, {})
    if not isinstance(raw, dict):
        return {}, {"broad-areas-full": ["broad area mapping must be an object"]}
    areas: dict[str, str] = {}
    errors: dict[str, list[str]] = {}
    for broad_id, value in raw.items():
        if not isinstance(value, dict):
            errors[str(broad_id)] = ["broad area guide must be an object"]
            continue
        messages: list[str] = []
        if not isinstance(value.get("title"), str) or not value["title"].strip():
            messages.append("title must be a non-empty string")
        if value.get("model") != "gpt-6-astra":
            messages.append("model must be 'gpt-6-astra'")
        if not isinstance(value.get("source"), str) or not value["source"].strip():
            messages.append("source must be a non-empty string")
        if value.get("corpusFingerprint") != fingerprint:
            messages.append("corpusFingerprint does not match the current full corpus")
        if messages:
            errors[str(broad_id)] = messages
        else:
            areas[str(broad_id)] = value["title"].strip()
    return areas, errors


def _import_full_topics(settings: Settings, path: Path, fingerprint: str) -> dict:
    """Apply only complete, Astra-attributed guides to the full topic hierarchy."""
    topics = read_json(path, [])
    if not isinstance(topics, list):
        raise ValueError("full topic artifact must be a list")
    guides, errors = _load_full_guides(settings)
    broad_areas, broad_errors = _load_full_broad_areas(settings, fingerprint)
    errors.update({f"broad:{key}": value for key, value in broad_errors.items()})
    known = {topic.get("slug") for topic in topics if isinstance(topic, dict)}
    for slug in set(guides) - known:
        errors[slug] = ["topic slug is absent from the current full clustering"]
    applied = 0
    pending = 0
    for topic in topics:
        slug = topic.get("slug")
        guide = guides.get(slug)
        messages = (
            _valid_full_guide(guide, fingerprint) if guide is not None else ["guide is missing"]
        )
        broad_id = topic.get("broadGroupId")
        if broad_id not in broad_areas:
            messages = [*messages, f"broad area {broad_id!r} is missing or invalid"]
        if messages:
            errors.setdefault(str(slug), messages)
            topic["curationStatus"] = "pending"
            pending += 1
            continue
        for field in _FULL_GUIDE_FIELDS:
            topic[field] = guide[field]
        topic["area"] = broad_areas[broad_id]
        topic["curationStatus"] = "complete"
        topic["provenance"] = {
            "source": guide["source"],
            "model": guide["model"],
            "corpusFingerprint": guide["corpusFingerprint"],
        }
        applied += 1
    write_json(path, topics)
    write_json(settings.derived_dir / "topic-import-errors-full.json", errors)
    return {"topics": len(topics), "overlays": applied, "pending": pending, "errors": errors}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "research-topic"


def _topic_count(corpus: str, papers: int) -> int:
    """Keep the reviewed preview stable while making the full map useful to browse."""
    if corpus == "full":
        return min(papers, 256)
    return min(papers, max(2, round(math.sqrt(papers / 3))))


def _representatives(
    papers: list[dict],
    indices: np.ndarray,
    vectors: np.ndarray,
    center: np.ndarray,
    *,
    limit: int = 12,
) -> list[dict]:
    """Give topic authors compact, varied evidence rather than a title-only label."""
    ordered = sorted(
        indices.tolist(),
        key=lambda index: (-float(vectors[index] @ center), papers[index]["slug"]),
    )
    chosen = ordered[:6]
    chronological = sorted(
        indices.tolist(), key=lambda index: (papers[index]["year"], papers[index]["slug"])
    )
    for index in chronological[:3] + chronological[-3:]:
        if index not in chosen and len(chosen) < limit:
            chosen.append(index)
    return [
        {
            "slug": papers[index]["slug"],
            "title": papers[index]["title"],
            "abstract": papers[index].get("abstract", ""),
            "year": papers[index]["year"],
        }
        for index in chosen
    ]


def _write_full_curation_input(
    settings: Settings,
    corpus_fingerprint: str,
    papers: list[dict],
    vectors: np.ndarray,
    labels: np.ndarray,
    centers: np.ndarray,
    topics: list[dict],
    broad_labels: np.ndarray,
) -> None:
    fine_rows: list[dict] = []
    by_broad: dict[str, list[str]] = defaultdict(list)
    for index, topic in enumerate(topics):
        members = np.flatnonzero(labels == index)
        broad_id = f"broad-{int(broad_labels[index]):02d}"
        by_broad[broad_id].append(topic["slug"])
        years = [papers[row]["year"] for row in members]
        fine_rows.append(
            {
                "topicSlug": topic["slug"],
                "broadGroupId": broad_id,
                "count": len(members),
                "yearSpan": {"start": min(years), "end": max(years)},
                "representatives": _representatives(papers, members, vectors, centers[index]),
            }
        )
    broad_rows = [
        {"broadGroupId": group, "childTopicSlugs": sorted(children)}
        for group, children in sorted(by_broad.items())
    ]
    write_json(
        settings.derived_dir / "topics-curation-input-full.json",
        {
            "corpus": "full",
            "corpusFingerprint": corpus_fingerprint,
            "fineTopics": fine_rows,
            "broadGroups": broad_rows,
        },
    )
    write_json(
        settings.derived_dir / "broad-topics-full.json",
        [
            {
                "id": group["broadGroupId"],
                "title": "Editorial curation pending",
                "curationStatus": "pending",
                "topicSlugs": group["childTopicSlugs"],
            }
            for group in broad_rows
        ],
    )


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
    n_clusters = _topic_count(corpus, len(papers))
    model = KMeans(n_clusters=n_clusters, random_state=settings.sample_seed, n_init="auto").fit(
        vectors
    )
    labels = model.labels_
    groups: dict[int, list[dict]] = defaultdict(list)
    for paper, label in zip(papers, labels, strict=True):
        groups[int(label)].append(paper)
    prior_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    prior_rows = (
        read_json(prior_path, []) if meta_matches(prior_path, corpus, corpus_fingerprint) else []
    )
    prior_papers = {
        paper["slug"]: paper
        for paper in prior_rows
        if isinstance(paper, dict) and paper.get("slug")
    }
    for paper in papers:
        previous = prior_papers.get(paper["slug"], {})
        if previous.get("summary"):
            paper["summary"] = previous["summary"]
    old_path = settings.derived_dir / f"topics-{corpus}.json"
    old_rows = read_json(old_path, []) if meta_matches(old_path, corpus, corpus_fingerprint) else []
    old_topics = {
        topic["slug"]: topic for topic in old_rows if isinstance(topic, dict) and topic.get("slug")
    }
    # Use member means for neighbour scores, preserving the reviewed sample's
    # prior clustering output exactly. KMeans centres can differ slightly from
    # a member mean when the iteration stops on its convergence tolerance.
    centers = np.array([vectors[labels == index].mean(axis=0) for index in sorted(groups)])
    centers = centers.astype(np.float32)
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-12)
    broad_labels = np.zeros(n_clusters, dtype=np.int32)
    if corpus == "full" and n_clusters > 1:
        broad_count = min(32, n_clusters)
        broad_labels = KMeans(
            n_clusters=broad_count, random_state=settings.sample_seed, n_init="auto"
        ).fit_predict(centers)
    topics: list[dict] = []
    for label, group in sorted(groups.items()):
        representative = sorted(group, key=lambda p: (-(p.get("citationCount") or 0), p["title"]))[
            0
        ]
        title = representative["title"][:80]
        slug = f"topic-{label:03d}-{_slug(title)[:48]}"
        for paper in group:
            paper["topicSlugs"] = [slug]
            previous = prior_papers.get(paper["slug"], {})
            background = (previous.get("summary") or {}).get("backgroundConcepts", [])
            paper["topicSlugs"] = sorted(set(paper["topicSlugs"] + background))
        topic = {
            "slug": slug,
            "title": title,
            "area": "Editorial curation pending"
            if corpus == "full"
            else "Machine learning research",
            "overview": f"A cluster of {len(group)} papers related to {title}.",
            "whyItMatters": "Editorial overview pending the approved summary pass.",
            "evolution": "Derived from paper embedding similarity.",
            "openQuestions": [],
            "prerequisites": [],
            "difficulty": "intermediate",
            "paperSlugs": [p["slug"] for p in sorted(group, key=lambda p: p["year"])],
            "relatedTopics": [],
        }
        if corpus == "full":
            topic["broadGroupId"] = f"broad-{int(broad_labels[label]):02d}"
            topic["curationStatus"] = "pending"
        topics.append(topic)
    # Preserve imported editorial prose when deterministic clustering is rerun;
    # membership is always recomputed from the current vectors.
    editable = {
        "title",
        "area",
        "overview",
        "whyItMatters",
        "evolution",
        "openQuestions",
        "prerequisites",
        "difficulty",
        "provenance",
    }
    for topic in topics:
        previous = old_topics.get(topic["slug"], {})
        if previous.get("provenance"):
            for key in editable:
                if key in previous:
                    topic[key] = previous[key]
    # centroid cosine neighbours
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
    if corpus == "full":
        _write_full_curation_input(
            settings,
            corpus_fingerprint,
            papers,
            vectors,
            labels,
            centers,
            topics,
            broad_labels,
        )
    return {
        "topics": len(topics),
        "broadGroups": len(set(broad_labels.tolist())) if corpus == "full" else 0,
        "papers": len(papers),
    }
