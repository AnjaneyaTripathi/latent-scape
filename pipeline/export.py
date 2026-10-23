from __future__ import annotations

import shutil
from collections import Counter, defaultdict
from urllib.parse import urlparse

from .settings import Settings
from .corpus import select, verify_meta
from .summaries import summary_eligibility_reason
from .util import read_json, shard_for, write_json


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_dblp_url(value: object) -> bool:
    if not _is_http_url(value):
        return False
    host = urlparse(str(value)).netloc.lower().split(":", 1)[0]
    return host == "dblp.org" or host.endswith(".dblp.org")


def _verified_correction_urls(settings: Settings) -> dict[str, str]:
    """Load only the reviewed source-repair links, never infer replacement URLs."""
    correction_dir = settings.root / "data" / "generated"
    candidates = sorted(correction_dir.glob("source-correction-*.json"))
    if not candidates:
        return {}
    artifact = read_json(candidates[-1], {})
    resolved: dict[str, str] = {}
    for update in artifact.get("updates", []):
        source = update.get("source")
        page = update.get("pdfStartPage")
        if isinstance(update.get("slug"), str) and _is_http_url(source) and isinstance(page, int) and page > 0:
            resolved[update["slug"]] = f"{source}#page={page}"
    return resolved


def _resolve_source_link(paper: dict, corrections: dict[str, str]) -> tuple[str | None, str]:
    """Return a truthful outbound link and its UI label.

    DBLP is valuable bibliographic metadata, but it is not an original paper
    destination.  Prefer reviewed repairs and recorded publisher/source URLs;
    do not construct or guess links from titles or identifiers.
    """
    candidates: list[object] = [
        paper.get("verifiedOriginalUrl"),
        corrections.get(str(paper.get("slug") or "")),
    ]
    candidates.extend(paper.get("sourceUrls") or [])
    candidates.extend([paper.get("url"), paper.get("abstractSource")])
    for candidate in candidates:
        if _is_http_url(candidate) and not _is_dblp_url(candidate):
            return str(candidate), "Original paper"
    fallback = paper.get("url")
    if _is_http_url(fallback):
        return str(fallback), "Publication record"
    return None, "Publication record"


def _summary_coverage(papers: list[dict]) -> dict[str, int]:
    """Report editorial state without treating absent prose as a summary."""
    narrative = legacy = pending = unavailable = 0
    for paper in papers:
        summary = paper.get("summary")
        if summary:
            if summary.get("paragraphs"):
                narrative += 1
            else:
                legacy += 1
            continue
        if summary_eligibility_reason(
            str(paper.get("abstract") or ""), str(paper.get("abstractQuality") or "")
        ):
            unavailable += 1
        else:
            pending += 1
    return {
        "narrative": narrative,
        "legacy": legacy,
        "pending": pending,
        "unavailable": unavailable,
    }


def _featured_slugs(papers: list[dict], count: int = 6) -> list[str]:
    """Choose stable, available starting points spanning venue and era."""
    candidates = [paper for paper in papers if paper.get("summary") and paper.get("abstract")]
    if not candidates:
        candidates = [paper for paper in papers if paper.get("abstract")]
    targets = [("NeurIPS", 1992), ("ICML", 2001), ("NeurIPS", 2011), ("ICML", 2018), ("NeurIPS", 2022), ("ICML", 2025)]
    selected: list[dict] = []
    for venue, year in targets:
        choices = [paper for paper in candidates if paper["venue"] == venue and paper["slug"] not in {item["slug"] for item in selected}]
        if choices:
            selected.append(min(choices, key=lambda paper: (abs(paper["year"] - year), paper["slug"])))
    for paper in sorted(candidates, key=lambda item: (item["year"], item["venue"], item["slug"])):
        if len(selected) >= count:
            break
        if paper["slug"] not in {item["slug"] for item in selected}:
            selected.append(paper)
    return [paper["slug"] for paper in selected[:count]]


def export_site_data(settings: Settings, corpus: str = "sample") -> dict:
    source_papers, value = select(settings, corpus)
    papers_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    topics_path = settings.derived_dir / f"topics-{corpus}.json"
    if papers_path.exists():
        verify_meta(papers_path, corpus, value, "topic papers")
        papers = read_json(papers_path, [])
    else:
        papers = source_papers
    if topics_path.exists():
        verify_meta(topics_path, corpus, value, "topics")
        topics = read_json(topics_path, [])
    else:
        topics = []
    coverage = read_json(settings.raw_dir / "coverage.json", {})
    output = settings.output_dir
    output.mkdir(parents=True, exist_ok=True)
    papers_dir = output / "papers"
    if papers_dir.exists():
        shutil.rmtree(papers_dir)
    papers_dir.mkdir()
    corrections = _verified_correction_urls(settings)
    shards: dict[str, list[dict]] = defaultdict(list)
    source_link_counts: Counter[str] = Counter()
    for paper in papers:
        public = dict(paper)
        original_url, source_link_label = _resolve_source_link(paper, corrections)
        public["originalUrl"] = original_url
        public["sourceLinkLabel"] = source_link_label
        source_link_counts[source_link_label] += 1
        shards[shard_for(paper["slug"])].append(public)
    paper_manifest = {}
    for prefix, values in shards.items():
        path = papers_dir / f"{prefix}.json"
        write_json(path, values)
        paper_manifest[prefix] = f"papers/{prefix}.json"
    search = [
        {"id": paper["slug"], "slug": paper["slug"], "type": "paper", "title": paper["title"],
         "abstract": paper["abstract"], "url": paper["url"], "venue": paper["venue"], "year": paper["year"], "topicSlugs": paper["topicSlugs"],
         "originalUrl": _resolve_source_link(paper, corrections)[0],
         "sourceLinkLabel": _resolve_source_link(paper, corrections)[1]}
        for paper in papers
    ]
    search.extend({"id": topic["slug"], "slug": topic["slug"], "type": "topic", "title": topic["title"],
                   "description": topic["overview"], "topicSlugs": [topic["slug"]]} for topic in topics)
    counts = Counter((paper["venue"], str(paper["year"])) for paper in papers)
    summary_coverage = _summary_coverage(papers)
    # Full-corpus topic prose is only publishable after an explicit curation
    # completion marker.  The reviewed sample predates that field, where
    # Astra provenance is the corresponding evidence of curation.
    if corpus == "full":
        curated_topics = sum(topic.get("curationStatus") == "complete" for topic in topics)
    else:
        curated_topics = sum(bool(topic.get("overview")) and bool(topic.get("provenance")) for topic in topics)
    report = {"papers": len(papers), "topics": len(topics), "curatedTopics": curated_topics,
              "summaryCoverage": summary_coverage, "sample": len(papers) <= settings.sample_size,
              "papersByVenueYear": {f"{venue}:{year}": count for (venue, year), count in sorted(counts.items())},
              "citationMatchRate": round(sum(p["citationCount"] is not None for p in papers) / len(papers), 4) if papers else 0,
              "sourceLinks": {"directOriginalPapers": source_link_counts["Original paper"],
                              "publicationRecords": source_link_counts["Publication record"]}}
    # Keep full abstracts in paper shards. Search snippets remain bounded at full
    # corpus scale, below Pages' individual asset limit.
    for document in search:
        if document.get("type") == "paper":
            document["abstract"] = document["abstract"][:480]
    vectors_file = settings.derived_dir / f"embeddings-{corpus}.npy"
    vector_manifest: dict = {"dimensions": 0, "scale": 127, "shards": []}
    if vectors_file.exists():
        verify_meta(vectors_file, corpus, value, "embeddings")
        import numpy as np
        from .embedding import quantize_unit_vectors
        vectors = np.load(vectors_file)
        if len(vectors) != len(papers):
            raise ValueError("embedding row count does not match exported papers")
        encoded = quantize_unit_vectors(vectors)
        vectors_dir = output / "vectors"
        if vectors_dir.exists():
            shutil.rmtree(vectors_dir)
        vectors_dir.mkdir()
        # 16 MiB upper bound: number of int8 rows determined by dimensions.
        rows = max(1, (16 * 1024 * 1024) // encoded.shape[1])
        for start in range(0, len(encoded), rows):
            end = min(start + rows, len(encoded))
            filename = f"vectors/{start // rows:04d}.i8"
            (output / filename).write_bytes(encoded[start:end].tobytes())
            vector_manifest["shards"].append({"path": filename, "ids": [p["slug"] for p in papers[start:end]]})
        vector_manifest["dimensions"] = int(encoded.shape[1])
    write_json(output / "vectors.json", vector_manifest)
    write_json(output / "search.json", search)
    write_json(output / "topics.json", topics)
    glossary = read_json(settings.derived_dir / f"glossary-{corpus}.json", [])
    write_json(output / "glossary.json", glossary)
    write_json(output / "coverage.json", coverage)
    write_json(output / "corpus-report.json", report)
    write_json(output / "manifest.json", {"snapshot": settings.snapshot_date, "totalPapers": len(papers), "totalTopics": len(topics), "coverage": coverage, "featuredSlugs": _featured_slugs(papers), "shards": {"papers": paper_manifest, "vectors": "vectors.json"}})
    return report
