from __future__ import annotations

import numpy as np

from .corpus import select, verify_meta, write_meta
from .settings import Settings
from .util import read_json, write_json


def similar(settings: Settings, corpus: str = "sample", k: int = 8, block_size: int = 512) -> dict:
    """Top-k cosine neighbors with bounded score matrices, suitable for full corpus."""
    _, value = select(settings, corpus)
    papers_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    verify_meta(papers_path, corpus, value, "topic papers")
    papers = read_json(papers_path, [])
    vectors_path = settings.derived_dir / f"embeddings-{corpus}.npy"
    verify_meta(vectors_path, corpus, value, "embeddings")
    vectors = np.load(vectors_path).astype(np.float32)
    if len(papers) != len(vectors):
        raise ValueError("similarity vectors do not match selected papers")
    count = min(k, max(0, len(papers) - 1))
    if count == 0:
        for paper in papers:
            paper["similarPapers"] = []
        write_json(papers_path, papers)
        write_meta(papers_path, corpus, value)
        return {"papers": len(papers), "k": 0}
    values = np.empty((len(papers), count), dtype=np.int32)
    for start in range(0, len(papers), block_size):
        stop = min(start + block_size, len(papers))
        scores = vectors[start:stop] @ vectors.T
        for row, index in enumerate(range(start, stop)):
            scores[row, index] = -np.inf
        choice = np.argpartition(scores, -values.shape[1], axis=1)[:, -values.shape[1]:]
        order = np.take_along_axis(scores, choice, axis=1).argsort(axis=1)[:, ::-1]
        values[start:stop] = np.take_along_axis(choice, order, axis=1)
    for paper, neighbours in zip(papers, values, strict=True):
        paper["similarPapers"] = [papers[int(index)]["slug"] for index in neighbours]
    write_json(papers_path, papers)
    write_meta(papers_path, corpus, value)
    return {"papers": len(papers), "k": int(values.shape[1])}
