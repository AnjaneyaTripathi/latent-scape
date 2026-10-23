from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def stable_slug(title: str, venue: str, year: int) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:88]
    digest = hashlib.sha256(f"{venue}|{year}|{title.casefold()}".encode()).hexdigest()[:10]
    return f"{venue.lower()}-{year}-{stem or 'untitled'}-{digest}"


def paper_key(title: str, year: int) -> str:
    text = re.sub(r"[^a-z0-9]+", "", title.casefold())
    return f"{year}:{text}"


def shard_for(slug: str) -> str:
    return hashlib.sha256(slug.encode()).hexdigest()[:2]


def cached_get(client: Any, url: str, cache_path: Path, delay: float) -> tuple[str | None, str | None]:
    """Return cached/remote text and a failure message. Failures are cacheable only as logs."""
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8"), None
    try:
        response = client.get(url)
        response.raise_for_status()
    except Exception as exc:  # network failures must remain visible in coverage.
        return None, f"{type(exc).__name__}: {exc}"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(response.text, encoding="utf-8")
    if delay:
        time.sleep(delay)
    return response.text, None
