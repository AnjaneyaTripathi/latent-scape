import json

import numpy as np

from pipeline.corpus import fingerprint, write_meta
from pipeline.embedding import embed_corpus
from pipeline.settings import Settings
from pipeline.topics import cluster, import_topics
from pipeline.util import write_json


def _settings(tmp_path):
    return Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "output",
    )


def _paper(index: int) -> dict:
    return {
        "slug": f"paper-{index:03d}",
        "title": f"Paper {index}",
        "abstract": f"A source abstract about method {index}.",
        "year": 2000 + index % 20,
        "venue": "ICML" if index % 2 else "NeurIPS",
        "url": f"https://example.test/{index}",
        "authors": ["Author"],
        "citationCount": index,
        "provenance": [],
    }


def test_full_embedding_resumes_source_bound_chunks(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    papers = [_paper(index) for index in range(5)]
    write_json(settings.raw_dir / "papers-full.json", papers)
    calls: list[list[str]] = []

    monkeypatch.setattr("pipeline.embedding.model_fingerprint", lambda *_: "model-fingerprint")

    def fake_embed(values, **_):
        current = list(values)
        calls.append(current)
        result = np.zeros((len(current), 384), dtype=np.float32)
        result[:, 0] = 1
        return result

    monkeypatch.setattr("pipeline.embedding.embed_texts", fake_embed)
    first = embed_corpus(settings, "full", chunk_size=2)
    assert first["rebuiltVectors"] == 5
    assert len(calls) == 3
    second = embed_corpus(settings, "full", chunk_size=2)
    assert second["reusedVectors"] == 5
    assert len(calls) == 3

    papers[2]["abstract"] = "A corrected official abstract."
    write_json(settings.raw_dir / "papers-full.json", papers)
    third = embed_corpus(settings, "full", chunk_size=2)
    assert third["rebuiltVectors"] == 2
    assert third["reusedVectors"] == 3
    assert len(calls) == 4
    progress = json.loads((settings.derived_dir / "embedding-progress-full.json").read_text())
    assert progress["completed"] == 5


def test_full_cluster_writes_broad_groups_and_bounded_curation_evidence(tmp_path):
    settings = _settings(tmp_path)
    # More than 32 rows exercises the fine/broad hierarchy without requiring a
    # production-scale KMeans fixture.
    papers = [_paper(index) for index in range(40)]
    write_json(settings.raw_dir / "papers-full.json", papers)
    value = fingerprint(papers)
    vectors = np.eye(40, dtype=np.float32)
    path = settings.derived_dir / "embeddings-full.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, vectors)
    write_meta(path, "full", value)

    result = cluster(settings, "full")
    assert result == {"topics": 40, "broadGroups": 32, "papers": 40}
    topics = json.loads((settings.derived_dir / "topics-full.json").read_text())
    assert all(topic["curationStatus"] == "pending" for topic in topics)
    assert all(topic["broadGroupId"].startswith("broad-") for topic in topics)
    evidence = json.loads((settings.derived_dir / "topics-curation-input-full.json").read_text())
    assert evidence["corpus"] == "full"
    assert evidence["corpusFingerprint"] == value
    assert len(evidence["fineTopics"]) == 40
    assert len(evidence["broadGroups"]) == 32
    assert all(len(topic["representatives"]) <= 12 for topic in evidence["fineTopics"])
    assert all(
        set(item) == {"slug", "title", "abstract", "year"}
        for topic in evidence["fineTopics"]
        for item in topic["representatives"]
    )


def test_full_topic_import_requires_complete_astra_guides_and_broad_areas(tmp_path):
    settings = _settings(tmp_path)
    papers = [_paper(0), _paper(1)]
    write_json(settings.raw_dir / "papers-full.json", papers)
    value = fingerprint(papers)
    topics_path = settings.derived_dir / "topics-full.json"
    write_json(
        topics_path,
        [
            {"slug": "topic-one", "broadGroupId": "broad-00", "curationStatus": "pending"},
            {"slug": "topic-two", "broadGroupId": "broad-00", "curationStatus": "pending"},
        ],
    )
    write_meta(topics_path, "full", value)
    generated = settings.root / "data/generated"
    complete = {
        "title": "Representation learning",
        "overview": "How learned representations organize relevant structure.",
        "whyItMatters": "It makes downstream prediction and transfer more effective.",
        "evolution": "The evidence spans multiple years of methods.",
        "openQuestions": ["Which representations transfer reliably?"],
        "prerequisites": ["Linear algebra"],
        "difficulty": "intermediate",
        "source": "cluster representatives",
        "model": "gpt-6-astra",
        "corpusFingerprint": value,
    }
    # The second guide intentionally lacks the required evolution field.
    incomplete = {key: item for key, item in complete.items() if key != "evolution"}
    write_json(generated / "topics-full-000.json", {"topic-one": complete, "topic-two": incomplete})
    write_json(
        generated / "broad-areas-full.json",
        {
            "broad-00": {
                "title": "Representation and learning",
                "source": "fine topic evidence",
                "model": "gpt-6-astra",
                "corpusFingerprint": value,
            }
        },
    )

    imported = import_topics(settings, "full")
    assert imported["overlays"] == 1
    assert imported["pending"] == 1
    result = json.loads(topics_path.read_text())
    assert result[0]["curationStatus"] == "complete"
    assert result[0]["area"] == "Representation and learning"
    assert result[0]["provenance"]["model"] == "gpt-6-astra"
    assert result[1]["curationStatus"] == "pending"
    assert "missing required field: evolution" in imported["errors"]["topic-two"]
