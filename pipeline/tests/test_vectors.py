import json

import numpy as np

from pipeline.corpus import fingerprint, write_meta
from pipeline.embedding import quantize_unit_vectors
from pipeline.export import (
    export_site_data,
    normalize_optional_outbound_url,
    repair_optional_outbound_links,
)
from pipeline.settings import Settings
from pipeline.util import write_json


def test_export_uses_scoped_embeddings_not_legacy_vectors(tmp_path):
    settings = Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "output",
    )
    papers = [
        {
            "slug": "paper-a",
            "title": "Paper A",
            "abstract": "A compact abstract.",
            "url": "https://example.test/a",
            "venue": "ICML",
            "year": 2024,
            "topicSlugs": [],
            "citationCount": None,
        },
        {
            "slug": "paper-b",
            "title": "Paper B",
            "abstract": "Another compact abstract.",
            "url": "https://example.test/b",
            "venue": "NeurIPS",
            "year": 2025,
            "topicSlugs": [],
            "citationCount": None,
        },
    ]
    write_json(settings.raw_dir / "papers-sample.json", papers)
    corpus_fingerprint = fingerprint(papers)
    vectors = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0]], dtype=np.float32)
    scoped_path = settings.derived_dir / "embeddings-sample.npy"
    scoped_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(scoped_path, vectors)
    write_meta(scoped_path, "sample", corpus_fingerprint)

    # A same-shaped legacy file must never become the export source.
    np.save(settings.derived_dir / "embeddings.npy", np.zeros_like(vectors))

    export_site_data(settings, "sample")

    manifest = json.loads((settings.output_dir / "vectors.json").read_text())
    ids = [slug for shard in manifest["shards"] for slug in shard["ids"]]
    payload = b"".join(
        (settings.output_dir / shard["path"]).read_bytes() for shard in manifest["shards"]
    )
    assert ids == [paper["slug"] for paper in papers]
    assert np.frombuffer(payload, dtype=np.int8).reshape(vectors.shape).tobytes() == quantize_unit_vectors(vectors).tobytes()


def test_export_marks_dblp_as_record_and_uses_reviewed_original_sources(tmp_path):
    settings = Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "output",
    )
    papers = [
        {
            "slug": "repaired", "title": "Repaired", "abstract": "A real abstract.",
            "url": "https://dblp.org/rec/conf/test/repaired", "venue": "NeurIPS", "year": 1991,
            "topicSlugs": [], "citationCount": None,
        },
        {
            "slug": "record-only", "title": "Record only", "abstract": "Another real abstract.",
            "url": "https://dblp.org/rec/conf/test/record", "venue": "ICML", "year": 1992,
            "topicSlugs": [], "citationCount": None,
        },
    ]
    write_json(settings.raw_dir / "papers-full.json", papers)
    corpus_fingerprint = fingerprint(papers)
    derived_papers = settings.derived_dir / "papers-topics-full.json"
    write_json(derived_papers, papers)
    write_meta(derived_papers, "full", corpus_fingerprint)
    topics_path = settings.derived_dir / "topics-full.json"
    topics = [
        {"slug": "pending", "title": "Pending", "overview": "A cluster of related papers.", "curationStatus": "pending"},
        {"slug": "complete", "title": "Complete", "overview": "Reviewed overview.", "curationStatus": "complete"},
    ]
    write_json(topics_path, topics)
    write_meta(topics_path, "full", corpus_fingerprint)
    write_json(
        settings.root / "data" / "generated" / "source-correction-test.json",
        {"updates": [{"slug": "repaired", "source": "https://publisher.test/paper.pdf", "pdfStartPage": 7}]},
    )

    report = export_site_data(settings, "full")

    repaired = next(
        paper for shard in (settings.output_dir / "papers").glob("*.json")
        for paper in json.loads(shard.read_text()) if paper["slug"] == "repaired"
    )
    record = next(
        paper for shard in (settings.output_dir / "papers").glob("*.json")
        for paper in json.loads(shard.read_text()) if paper["slug"] == "record-only"
    )
    assert repaired["originalUrl"] == "https://publisher.test/paper.pdf#page=7"
    assert repaired["sourceLinkLabel"] == "Original paper"
    assert record["sourceLinkLabel"] == "Publication record"
    assert report["sourceLinks"] == {"directOriginalPapers": 1, "publicationRecords": 1}
    assert report["curatedTopics"] == 1
    search = json.loads((settings.output_dir / "search.json").read_text())
    assert next(item for item in search if item["slug"] == "repaired")["originalUrl"] == repaired["originalUrl"]


def test_export_strips_editorial_provenance_from_browser_records(tmp_path):
    settings = Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "output",
    )
    papers = [{
        "slug": "reader-paper", "title": "Reader Paper", "authors": ["Author"],
        "abstract": "A source abstract.", "originalAbstract": "A source abstract.",
        "abstractStatus": "present", "abstractQuality": "official-landing-page",
        "abstractSource": "internal-source-note", "sourceRecoveryLocked": True,
        "summaryEvidenceStatus": "accepted", "provenance": ["official"],
        "url": "https://example.test/paper", "venue": "ICML", "year": 2024,
        "topicSlugs": ["reader-topic"], "citationCount": 3,
        "summary": {
            "tldr": "A complete published explanation. " * 30, "paragraphs": ["One.", "Two.", "Three."],
            "difficulty": "beginner", "model": "gpt-6-astra", "source": "abstract",
            "sourceAbstractSha256": "secret-editorial-hash", "backgroundConcepts": ["reader-topic"],
        },
    }]
    write_json(settings.raw_dir / "papers-full.json", papers)
    corpus_fingerprint = fingerprint(papers)
    papers_path = settings.derived_dir / "papers-topics-full.json"
    write_json(papers_path, papers)
    write_meta(papers_path, "full", corpus_fingerprint)
    topics_path = settings.derived_dir / "topics-full.json"
    write_json(topics_path, [{
        "slug": "reader-topic", "title": "Reader topic", "area": "Area", "broadGroupId": "broad-00",
        "overview": "A reader-facing overview.", "paperSlugs": ["reader-paper"], "relatedTopics": [],
        "curationStatus": "complete", "provenance": {"model": "gpt-6-astra"},
    }])
    write_meta(topics_path, "full", corpus_fingerprint)

    export_site_data(settings, "full")

    paper = next(
        item for path in (settings.output_dir / "papers").glob("*.json")
        for item in json.loads(path.read_text())
    )
    topic = json.loads((settings.output_dir / "topics.json").read_text())[0]
    search = json.loads((settings.output_dir / "search.json").read_text())
    paper_document = next(item for item in search if item["type"] == "paper")
    assert paper_document["tldr"] == paper["summary"]["tldr"] == papers[0]["summary"]["tldr"]
    assert "tldr" not in next(item for item in search if item["type"] == "topic")
    assert not {"provenance", "abstractSource", "sourceRecoveryLocked", "summaryEvidenceStatus"}.intersection(paper)
    assert not {"model", "source", "sourceAbstractSha256", "backgroundConcepts"}.intersection(paper["summary"])
    assert not {"provenance", "curationStatus"}.intersection(topic)
    assert topic["broadGroupId"] == "broad-00"
    assert "coverage" not in json.loads((settings.output_dir / "manifest.json").read_text())
    assert not any((settings.output_dir / name).exists() for name in ("coverage.json", "corpus-report.json", "report.json", "schema.json"))


def test_export_removes_legacy_review_page_from_configured_output(tmp_path):
    settings = Settings(
        root=tmp_path, cache_dir=tmp_path / "cache", raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived", output_dir=tmp_path / "output",
    )
    papers = [{"slug": "paper", "title": "Paper", "abstract": "A source abstract.", "url": "https://example.test/paper", "venue": "ICML", "year": 2024, "topicSlugs": [], "citationCount": 0}]
    write_json(settings.raw_dir / "papers-sample.json", papers)
    review = settings.output_dir / "summary-review.html"
    review.parent.mkdir(parents=True)
    review.write_text("legacy internal review")
    export_site_data(settings, "sample")
    assert not review.exists()


def test_optional_link_repair_normalizes_only_exact_duplicate_markdown_destinations(tmp_path):
    settings = Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "output",
    )
    papers = [
        {
            "slug": "recoverable",
            "title": "Recoverable",
            "abstract": "A real abstract.",
            "url": "https://example.test/paper",
            "venue": "NeurIPS",
            "year": 2023,
            "topicSlugs": [],
            "citationCount": None,
            "projectUrl": "https://project.test](https://project.test",
        },
        {
            "slug": "unsafe",
            "title": "Unsafe",
            "abstract": "Another real abstract.",
            "url": "https://example.test/other",
            "venue": "ICML",
            "year": 2023,
            "topicSlugs": [],
            "citationCount": None,
            "projectUrl": "javascript:alert(1)",
        },
    ]
    write_json(settings.raw_dir / "papers-full.json", papers)
    corpus_fingerprint = fingerprint(papers)
    derived_papers = settings.derived_dir / "papers-topics-full.json"
    write_json(derived_papers, papers)
    write_meta(derived_papers, "full", corpus_fingerprint)

    repair = repair_optional_outbound_links(settings, "full")

    repaired = json.loads(derived_papers.read_text())
    assert repaired[0]["projectUrl"] == "https://project.test"
    assert repaired[1]["projectUrl"] is None
    assert [update["slug"] for update in repair["updates"]] == ["recoverable", "unsafe"]
    assert (settings.root / "data" / "generated" / "optional-link-repairs-full.json").exists()
    assert normalize_optional_outbound_url("https://ok.test/project") == "https://ok.test/project"
    assert normalize_optional_outbound_url("https://one.test](https://two.test") is None

    export_site_data(settings, "full")
    values = [
        paper
        for path in (settings.output_dir / "papers").glob("*.json")
        for paper in json.loads(path.read_text())
    ]
    assert next(paper for paper in values if paper["slug"] == "recoverable")["projectUrl"] == "https://project.test"
    assert next(paper for paper in values if paper["slug"] == "unsafe")["projectUrl"] is None


def test_export_search_summary_uses_complete_public_tldr_only():
    from pipeline.export import _search_summary

    tldr = "This published TLDR must never be cut midword. " * 30
    assert _search_summary({"summary": {"tldr": tldr, "model": "private", "paragraphs": ["Longer explanation"]}}) == {"tldr": tldr}
    for summary in (None, {}, {"tldr": ""}, {"tldr": 42}):
        assert _search_summary({"summary": summary}) == {}
