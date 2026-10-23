from __future__ import annotations

import shutil
from collections import Counter, defaultdict

from .settings import Settings
from .corpus import select, verify_meta
from .util import read_json, shard_for, write_json


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
    shards: dict[str, list[dict]] = defaultdict(list)
    for paper in papers:
        public = dict(paper)
        shards[shard_for(paper["slug"])].append(public)
    paper_manifest = {}
    for prefix, values in shards.items():
        path = papers_dir / f"{prefix}.json"
        write_json(path, values)
        paper_manifest[prefix] = f"papers/{prefix}.json"
    search = [
        {"id": paper["slug"], "slug": paper["slug"], "type": "paper", "title": paper["title"],
         "abstract": paper["abstract"], "url": paper["url"], "venue": paper["venue"], "year": paper["year"], "topicSlugs": paper["topicSlugs"]}
        for paper in papers
    ]
    search.extend({"id": topic["slug"], "slug": topic["slug"], "type": "topic", "title": topic["title"],
                   "description": topic["overview"], "topicSlugs": [topic["slug"]]} for topic in topics)
    counts = Counter((paper["venue"], str(paper["year"])) for paper in papers)
    report = {"papers": len(papers), "topics": len(topics), "sample": len(papers) <= settings.sample_size,
              "papersByVenueYear": {f"{venue}:{year}": count for (venue, year), count in sorted(counts.items())},
              "citationMatchRate": round(sum(p["citationCount"] is not None for p in papers) / len(papers), 4) if papers else 0}
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
    write_json(output / "manifest.json", {"snapshot": settings.snapshot_date, "totalPapers": len(papers), "totalTopics": len(topics), "coverage": coverage, "shards": {"papers": paper_manifest, "vectors": "vectors.json"}})
    return report
