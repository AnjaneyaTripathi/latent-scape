from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EMBEDDING_MODEL = "Xenova/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class Settings:
    root: Path
    cache_dir: Path
    raw_dir: Path
    derived_dir: Path
    output_dir: Path
    snapshot_date: str = "2026-10-23"
    timeout: float = 30.0
    delay: float = 0.15
    sample_size: int = 500
    sample_seed: int = 20261023
    enrichment_provider: str = "none"
    enrichment_enabled: bool = False
    enrichment_max_requests: int = 0
    llm_provider: str = "disabled"
    llm_model: str = ""
    llm_max_usd: float = 0.0
    embedding_model: str = SUPPORTED_EMBEDDING_MODEL

    def prepare(self) -> None:
        for path in (self.cache_dir, self.raw_dir, self.derived_dir, self.output_dir):
            path.mkdir(parents=True, exist_ok=True)


def _path(root: Path, value: str) -> Path:
    return root / value


def load_settings(root: Path, config_path: Path | None = None) -> Settings:
    config_path = config_path or root / "config.toml"
    if not config_path.exists():
        config_path = root / "config.example.toml"
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    pipeline = raw.get("pipeline", {})
    ingest = raw.get("ingest", {})
    enrich = raw.get("enrich", {})
    llm = raw.get("llm", {})
    embedding = raw.get("embedding", {})
    embedding_model = str(embedding.get("model", SUPPORTED_EMBEDDING_MODEL))
    if embedding_model != SUPPORTED_EMBEDDING_MODEL:
        raise ValueError(
            f"Unsupported embedding model {embedding_model!r}. Latentscape ships "
            f"{SUPPORTED_EMBEDDING_MODEL!r} in both the offline pipeline and browser; "
            "changing it requires staging a matching browser model and updating the runtime."
        )

    return Settings(
        root=root,
        cache_dir=_path(root, pipeline.get("cache_dir", "pipeline/.cache")),
        raw_dir=_path(root, pipeline.get("raw_dir", "pipeline/data/raw")),
        derived_dir=_path(root, pipeline.get("derived_dir", "pipeline/data/derived")),
        output_dir=_path(root, pipeline.get("output_dir", "site/public/data")),
        snapshot_date=pipeline.get("snapshot_date", "2026-10-23"),
        timeout=float(pipeline.get("request_timeout_seconds", 30)),
        delay=float(pipeline.get("request_delay_seconds", 0.15)),
        sample_size=int(ingest.get("sample_size", 500)),
        sample_seed=int(ingest.get("sample_seed", 20261023)),
        enrichment_provider=str(enrich.get("provider", "none")),
        enrichment_enabled=bool(enrich.get("enabled", False)),
        enrichment_max_requests=int(enrich.get("max_requests", 0)),
        llm_provider=str(llm.get("provider", "disabled")),
        llm_model=str(llm.get("model", "")),
        llm_max_usd=float(llm.get("max_usd", 0)),
        embedding_model=embedding_model,
    )
