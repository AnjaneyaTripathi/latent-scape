"""Selected-corpus state and content fingerprints for resumable pipeline stages."""

from __future__ import annotations

import hashlib
import json

from .settings import Settings
from .util import read_json, write_json


def source_path(settings: Settings, corpus: str) -> object:
    if corpus not in {"sample", "full"}:
        raise ValueError("corpus must be 'sample' or 'full'")
    return settings.raw_dir / f"papers-{corpus}.json"


def fingerprint(papers: list[dict]) -> str:
    relevant = [
        {"slug": p["slug"], "title": p["title"], "abstract": p.get("abstract", "")}
        for p in papers
    ]
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def select(settings: Settings, corpus: str) -> tuple[list[dict], str]:
    papers = read_json(source_path(settings, corpus), None)
    if papers is None:
        raise FileNotFoundError(f"No {corpus} corpus found; run ingest-{'sample' if corpus == 'sample' else 'full'} first.")
    value = fingerprint(papers)
    write_json(settings.derived_dir / "active-corpus.json", {"corpus": corpus, "fingerprint": value, "papers": len(papers)})
    return papers, value


def active(settings: Settings, corpus: str) -> tuple[list[dict], str]:
    papers, value = select(settings, corpus)
    state = read_json(settings.derived_dir / "active-corpus.json", {})
    if state.get("corpus") != corpus or state.get("fingerprint") != value:
        raise RuntimeError("active corpus state is stale; rerun the requested stage")
    return papers, value


def stamped(payload: dict, corpus: str, value: str) -> dict:
    return {"_corpus": corpus, "_fingerprint": value, **payload}


def verify_stamp(payload: dict | list, corpus: str, value: str, name: str) -> None:
    if not isinstance(payload, dict) or payload.get("_corpus") != corpus or payload.get("_fingerprint") != value:
        raise RuntimeError(f"{name} belongs to a different corpus or source revision; rerun its stage")


def write_meta(path: object, corpus: str, value: str) -> None:
    write_json(path.with_suffix(path.suffix + ".meta.json"), {"corpus": corpus, "fingerprint": value})


def verify_meta(path: object, corpus: str, value: str, name: str) -> None:
    meta = read_json(path.with_suffix(path.suffix + ".meta.json"), {})
    if meta != {"corpus": corpus, "fingerprint": value}:
        raise RuntimeError(f"{name} belongs to a different corpus or source revision; rerun its stage")


def meta_matches(path: object, corpus: str, value: str) -> bool:
    return read_json(path.with_suffix(path.suffix + ".meta.json"), {}) == {"corpus": corpus, "fingerprint": value}
