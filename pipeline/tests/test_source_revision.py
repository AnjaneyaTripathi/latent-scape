import hashlib
import json

import numpy as np

from pipeline.corpus import fingerprint, write_meta
from pipeline.settings import Settings
from pipeline.source_revision import apply_source_revision
from pipeline.util import read_json, write_json


def _settings(tmp_path):
    return Settings(root=tmp_path, cache_dir=tmp_path / "cache", raw_dir=tmp_path / "raw", derived_dir=tmp_path / "derived", output_dir=tmp_path / "out")


def _paper(index):
    return {"slug": f"paper-{index:02d}", "title": f"Paper {index}", "venue": "ICML", "year": 2020, "abstract": f"old abstract {index}", "originalAbstract": f"old abstract {index}", "abstractStatus": "present", "abstractQuality": "official", "topicSlugs": ["topic-a" if index < 11 else "topic-b"]}


def test_source_revision_reassigns_only_repaired_papers_and_preserves_topic_ids(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.prepare()
    papers = [_paper(index) for index in range(21)]
    papers[20]["citationCount"] = 7
    papers[20]["provenance"] = ["OpenAlex"]
    raw_path = settings.raw_dir / "papers-full.json"
    write_json(raw_path, papers)
    value = fingerprint(papers)
    write_json(settings.derived_dir / "papers-topics-full.json", papers)
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", value)
    topics = [{"slug": "topic-a", "broadGroupId": "broad-00", "paperSlugs": [paper["slug"] for paper in papers[:11]], "relatedTopics": [], "provenance": {"corpusFingerprint": value}}, {"slug": "topic-b", "broadGroupId": "broad-01", "paperSlugs": [paper["slug"] for paper in papers[11:]], "relatedTopics": [], "provenance": {"corpusFingerprint": value}}]
    write_json(settings.derived_dir / "topics-full.json", topics)
    write_meta(settings.derived_dir / "topics-full.json", "full", value)
    old_vectors = np.zeros((21, 384), dtype=np.float32)
    old_vectors[:11, 0] = 1
    old_vectors[11:, 1] = 1
    np.save(settings.derived_dir / "embeddings-full.npy", old_vectors)
    write_meta(settings.derived_dir / "embeddings-full.npy", "full", value)
    write_json(settings.derived_dir / "topics-curation-input-full.json", {"fineTopics": []})
    updates = []
    for paper in papers:
        updates.append({"slug": paper["slug"], "title": paper["title"], "venue": paper["venue"], "year": paper["year"], "expectedOldAbstractSha256": hashlib.sha256(paper["abstract"].encode()).hexdigest(), "abstract": f"corrected abstract {paper['slug']}", "originalAbstract": f"corrected abstract {paper['slug']}", "abstractStatus": "present", "abstractSource": "https://example.test/paper.pdf", "source": "https://example.test/paper.pdf", "pdfStartPage": 1, "abstractQuality": "title-and-page-verified-original", "abstractRecoveryLocked": True, "abstractExtraction": "verified", "provenance": ["https://example.test/landing", "https://example.test/paper.pdf#page=1"], "sourceIssue": "collision", "collisionGroupId": "group"})
    repair = {"inputRawPath": "pipeline/data/raw/papers-full.json", "inputRawSha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(), "updates": updates, "unresolved": [], "sourcePolicy": "test", "validation": "test"}
    write_json(settings.raw_dir / "abstract-collision-repairs.json", repair)

    def fake_embed(current_settings, *_args, **_kwargs):
        vectors = np.zeros((21, 384), dtype=np.float32)
        vectors[:, 1] = 1
        np.save(current_settings.derived_dir / "embeddings-full.npy", vectors)
        write_meta(current_settings.derived_dir / "embeddings-full.npy", "full", fingerprint(read_json(current_settings.raw_dir / "papers-full.json", [])))
        return {"rebuiltVectors": 21, "reusedVectors": 0}

    monkeypatch.setattr("pipeline.source_revision.embed_corpus", fake_embed)
    result = apply_source_revision(settings)
    assert result["repairs"] == 21
    assert set(result["preservedTopicIds"]) == {"topic-a", "topic-b"}
    assert any(change["oldTopicSlug"] != change["newTopicSlug"] for change in result["reassignments"].values())
    revised_topics = read_json(settings.derived_dir / "topics-full.json", [])
    assert {paper for topic in revised_topics for paper in topic["paperSlugs"]} == {paper["slug"] for paper in papers}
    assert len(next(topic for topic in revised_topics if topic["slug"] == "topic-a")["paperSlugs"]) == 1
    assert len(next(topic for topic in revised_topics if topic["slug"] == "topic-b")["paperSlugs"]) == 20
    assert read_json(settings.raw_dir / "papers-full.json", [])[0]["abstract"].startswith("corrected")
    assert "OpenAlex" in next(paper for paper in read_json(settings.derived_dir / "papers-topics-full.json", []) if paper["slug"] == "paper-20")["provenance"]
