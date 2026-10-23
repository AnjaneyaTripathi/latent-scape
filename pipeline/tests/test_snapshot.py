from pathlib import Path

import pytest

from pipeline.ingest import frozen_sample_selection
from pipeline.settings import Settings, load_settings


def test_clean_raw_directory_uses_committed_reviewed_snapshot(tmp_path: Path):
    root = Path(__file__).parents[2]
    settings = Settings(root=root, cache_dir=tmp_path / "cache", raw_dir=tmp_path / "raw", derived_dir=tmp_path / "derived", output_dir=tmp_path / "output")
    selected = frozen_sample_selection(settings)
    assert len(selected) == 500
    assert selected[0]["slug"].startswith(("icml-", "neurips-"))
    assert frozen_sample_selection(settings, freeze_sample=False) == []


def test_settings_rejects_an_embedding_model_without_matching_browser_runtime(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('[embedding]\nmodel = "different/model"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported embedding model"):
        load_settings(tmp_path, config)
