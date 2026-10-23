#!/usr/bin/env python3
"""Fail closed if a Latentscape Pages artifact stops being self-contained.

Run after ``npm run build``.  The checks deliberately inspect the generated
artifact instead of the source tree: GitHub Pages serves only this directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# GitHub Pages documents these limits in decimal units. Using their exact values
# keeps the check conservative rather than silently permitting an oversized file.
MAX_FILE_BYTES = 100_000_000
MAX_SITE_BYTES = 1_000_000_000
MODEL_FILES = (
    "models/Xenova/all-MiniLM-L6-v2/tokenizer.json",
    "models/Xenova/all-MiniLM-L6-v2/onnx/model_quantized.onnx",
)
DATA_FILES = (
    "data/manifest.json",
    "data/search.json",
    "data/topics.json",
    "data/vectors.json",
    "data/coverage.json",
    "data/schema.json",
    "data/report.json",
)
TEXT_ASSET_SUFFIXES = {".html", ".js", ".css"}
REMOTE_ASSET = re.compile(r"https?://[^\"'\s)<>]+", re.IGNORECASE)
EXTERNAL_ASSET_SUFFIXES = (".js", ".mjs", ".css", ".wasm", ".onnx", ".json")


def error(errors: list[str], message: str) -> None:
    errors.append(message)


def require_json(path: Path, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        error(errors, f"invalid JSON: {path}: {exc}")
        return None


def has_remote_asset(value: Any, key: str = "") -> bool:
    """Permit research links, but reject a JSON field expressly used as an asset."""
    if isinstance(value, dict):
        return any(has_remote_asset(child, child_key) for child_key, child in value.items())
    if isinstance(value, list):
        return any(has_remote_asset(child, key) for child in value)
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return False
    lowered = value.lower().split("?", 1)[0]
    return any(lowered.endswith(suffix) for suffix in EXTERNAL_ASSET_SUFFIXES) or key.lower() in {
        "asset", "asseturl", "modelurl", "wasmurl", "scripturl", "stylesheeturl"
    }


def validate_corpus(root: Path, manifest: dict[str, Any], errors: list[str]) -> tuple[list[dict], list[dict], int]:
    """Check that static records form one closed, internally consistent atlas."""
    data = root / "data"
    search = require_json(data / "search.json", errors)
    topics = require_json(data / "topics.json", errors)
    vectors = require_json(data / "vectors.json", errors)
    if not isinstance(search, list) or not isinstance(topics, list) or not isinstance(vectors, dict):
        return [], [], 0
    papers: list[dict] = []
    for prefix, relative in (manifest.get("shards", {}).get("papers", {}) or {}).items():
        if not isinstance(relative, str) or not relative.startswith("papers/"):
            error(errors, f"invalid paper shard path for {prefix!r}")
            continue
        shard_path = data / relative
        shard = require_json(shard_path, errors)
        if not isinstance(shard, list):
            continue
        papers.extend(item for item in shard if isinstance(item, dict))

    paper_slugs = [paper.get("slug") for paper in papers]
    topic_slugs = [topic.get("slug") for topic in topics if isinstance(topic, dict)]
    if len(paper_slugs) != len(set(paper_slugs)) or any(not isinstance(slug, str) or not slug for slug in paper_slugs):
        error(errors, "paper shards contain duplicate or invalid slugs")
    if len(topic_slugs) != len(set(topic_slugs)) or any(not isinstance(slug, str) or not slug for slug in topic_slugs):
        error(errors, "topics contain duplicate or invalid slugs")
    if manifest.get("totalPapers") != len(papers):
        error(errors, f"manifest totalPapers ({manifest.get('totalPapers')}) does not match shards ({len(papers)})")
    if manifest.get("totalTopics") != len(topics):
        error(errors, f"manifest totalTopics ({manifest.get('totalTopics')}) does not match topics ({len(topics)})")

    paper_set, topic_set = set(paper_slugs), set(topic_slugs)
    search_ids = [item.get("id") for item in search if isinstance(item, dict)]
    if len(search_ids) != len(set(search_ids)):
        error(errors, "search index contains duplicate ids")
    indexed_papers = {item.get("slug") for item in search if isinstance(item, dict) and item.get("type") == "paper"}
    indexed_topics = {item.get("slug") for item in search if isinstance(item, dict) and item.get("type") == "topic"}
    if indexed_papers != paper_set:
        error(errors, "search paper ids do not exactly match paper shards")
    if indexed_topics != topic_set:
        error(errors, "search topic ids do not exactly match topics")

    summaries = 0
    for paper in papers:
        if not isinstance(paper.get("url"), str) or paper["url"].lower().split("?", 1)[0].endswith(".pdf"):
            error(errors, f"paper {paper.get('slug')} has no valid non-PDF original URL")
        for field in ("references", "citedBy", "similarPapers"):
            unknown = set(paper.get(field) or []) - paper_set
            if unknown:
                error(errors, f"paper {paper.get('slug')} has dangling {field}: {sorted(unknown)[:3]}")
        unknown_topics = set(paper.get("topicSlugs") or []) - topic_set
        if unknown_topics:
            error(errors, f"paper {paper.get('slug')} has dangling topics: {sorted(unknown_topics)[:3]}")
        citations = paper.get("citationCount")
        if citations is not None and (not isinstance(citations, int) or citations < 0):
            error(errors, f"paper {paper.get('slug')} has invalid citationCount")
        if citations is not None and not {"OpenAlex", "Semantic Scholar"}.intersection(paper.get("provenance") or []):
            error(errors, f"paper {paper.get('slug')} has citationCount without enrichment provenance")
        if paper.get("summary") is not None:
            summaries += 1
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        if set(topic.get("paperSlugs") or []) - paper_set:
            error(errors, f"topic {topic.get('slug')} has dangling paper reference")
        if set(topic.get("relatedTopics") or []) - topic_set:
            error(errors, f"topic {topic.get('slug')} has dangling related topic")

    dimensions, vector_shards = vectors.get("dimensions"), vectors.get("shards")
    if not isinstance(dimensions, int) or dimensions < 1 or not isinstance(vector_shards, list):
        error(errors, "vectors must declare positive dimensions and shards")
    else:
        vector_ids: list[str] = []
        for shard in vector_shards:
            if not isinstance(shard, dict) or not isinstance(shard.get("path"), str) or not isinstance(shard.get("ids"), list):
                error(errors, "invalid vector shard metadata")
                continue
            vector_path = data / shard["path"]
            expected = len(shard["ids"]) * dimensions
            if not vector_path.is_file() or vector_path.stat().st_size != expected:
                error(errors, f"vector shard shape mismatch: {shard.get('path')}")
            vector_ids.extend(shard["ids"])
        if len(vector_ids) != len(set(vector_ids)) or set(vector_ids) != paper_set:
            error(errors, "vector ids do not exactly match paper shards")
    return papers, topics, summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="site/dist", help="built Pages directory")
    parser.add_argument("--expected-summaries", type=int, help="fail unless this exact number of summaries is present")
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    errors: list[str] = []
    if not root.is_dir():
        print(f"artifact directory does not exist: {root}", file=sys.stderr)
        return 1

    files = [path for path in root.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    for path in files:
        relative = path.relative_to(root)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            error(errors, f"file exceeds 100 MB: {relative} ({size} bytes)")
        if path.suffix.lower() == ".pdf":
            error(errors, f"PDF found in static artifact: {relative}")
        if path.suffix.lower() in TEXT_ASSET_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                error(errors, f"non-UTF-8 text asset: {relative}")
                continue
            for url in REMOTE_ASSET.findall(text):
                if url.lower().split("?", 1)[0].endswith(EXTERNAL_ASSET_SUFFIXES):
                    error(errors, f"external generated asset URL in {relative}: {url}")
    if total > MAX_SITE_BYTES:
        error(errors, f"site exceeds 1 GB: {total} bytes")

    for relative in MODEL_FILES + DATA_FILES:
        if not (root / relative).is_file():
            error(errors, f"required static file missing: {relative}")
    if not any(path.suffix == ".wasm" for path in files):
        error(errors, "no local WASM runtime in artifact")

    index = root / "index.html"
    if index.is_file():
        content = index.read_text(encoding="utf-8")
        csp_match = re.search(
            r'''<meta[^>]+http-equiv=["']Content-Security-Policy["'][^>]+content=(["'])(.*?)\1''',
            content, re.IGNORECASE,
        )
        csp = csp_match.group(2) if csp_match else ""
        if "connect-src 'self'" not in csp:
            error(errors, "index.html must enforce CSP connect-src 'self'")
        if "script-src 'self'" not in csp:
            error(errors, "index.html must enforce CSP script-src 'self'")

    data_root = root / "data"
    manifest = require_json(data_root / "manifest.json", errors) if (data_root / "manifest.json").is_file() else None
    coverage = require_json(data_root / "coverage.json", errors) if (data_root / "coverage.json").is_file() else None
    report = require_json(data_root / "report.json", errors) if (data_root / "report.json").is_file() else None
    for filename in ("search.json", "topics.json", "vectors.json", "schema.json"):
        path = data_root / filename
        if path.is_file():
            parsed = require_json(path, errors)
            if has_remote_asset(parsed):
                error(errors, f"external generated asset URL in data/{filename}")
    if not isinstance(manifest, dict) or not manifest.get("snapshot"):
        error(errors, "manifest must declare snapshot")
    papers, topics, summaries = validate_corpus(root, manifest, errors) if isinstance(manifest, dict) else ([], [], 0)
    if args.expected_summaries is not None and summaries != args.expected_summaries:
        error(errors, f"expected {args.expected_summaries} summaries; found {summaries}")
    if not isinstance(coverage, dict) or not {"NeurIPS", "ICML"}.issubset(coverage):
        error(errors, "coverage audit must include NeurIPS and ICML")
    if not isinstance(report, dict) or not {"siteBytes", "papers", "topics", "coverage", "summaries", "model"}.issubset(report):
        error(errors, "report must include siteBytes, papers, topics, coverage, summaries, and model provenance")
    elif report.get("siteBytes") != total:
        error(errors, f"report siteBytes ({report.get('siteBytes')}) does not match built artifact ({total})")
    elif report.get("papers") != len(papers) or report.get("topics") != len(topics):
        error(errors, "report paper/topic counts do not match static data")
    elif report.get("summaries") != summaries:
        error(errors, "report summary count does not match static data")
    elif not isinstance(report.get("model"), dict) or not report["model"].get("revision"):
        error(errors, "report model provenance is incomplete")

    if errors:
        print("Static artifact validation failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in errors), file=sys.stderr)
        return 1
    print(json.dumps({"artifact": str(root), "files": len(files), "siteBytes": total}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
