import json

import numpy as np

from pipeline.corpus import fingerprint, write_meta
from pipeline.embedding import quantize_unit_vectors
from pipeline.export import export_site_data
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
