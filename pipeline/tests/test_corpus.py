from pathlib import Path

import pytest

from pipeline.corpus import fingerprint, verify_meta, write_meta


def test_fingerprint_changes_when_same_size_corpus_content_changes():
    left = [{"slug": "one", "title": "One", "abstract": "first"}]
    right = [{"slug": "one", "title": "One", "abstract": "second"}]
    assert fingerprint(left) != fingerprint(right)


def test_meta_rejects_wrong_corpus_even_with_same_file(tmp_path: Path):
    artifact = tmp_path / "embeddings.npy"
    artifact.write_bytes(b"x")
    write_meta(artifact, "sample", "abc")
    with pytest.raises(RuntimeError, match="different corpus"):
        verify_meta(artifact, "full", "abc", "embeddings")
