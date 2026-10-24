#!/usr/bin/env python3
"""Fail closed if a Latentscape Pages artifact stops being self-contained.

Run after ``npm run build``.  The checks deliberately inspect the generated
artifact instead of the source tree: GitHub Pages serves only this directory.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# GitHub Pages documents these limits in decimal units. Using their exact values
# keeps the check conservative rather than silently permitting an oversized file.
MAX_FILE_BYTES = 100_000_000
MAX_SITE_BYTES = 1_000_000_000
MODEL_FILES = (
    "models/Xenova/all-MiniLM-L6-v2/config.json",
    "models/Xenova/all-MiniLM-L6-v2/special_tokens_map.json",
    "models/Xenova/all-MiniLM-L6-v2/tokenizer.json",
    "models/Xenova/all-MiniLM-L6-v2/tokenizer_config.json",
    "models/Xenova/all-MiniLM-L6-v2/vocab.txt",
    "models/Xenova/all-MiniLM-L6-v2/onnx/model_quantized.onnx",
)
DATA_FILES = (
    "data/manifest.json",
    "data/search.json",
    "data/topics.json",
    "data/topic-graph.json",
    "data/vectors.json",
)
TEXT_ASSET_SUFFIXES = {".html", ".js", ".css"}
REMOTE_ASSET = re.compile(r"https?://[^\"'\s)<>]+", re.IGNORECASE)
EXTERNAL_ASSET_SUFFIXES = (".js", ".mjs", ".css", ".wasm", ".onnx", ".json")
FORBIDDEN_PUBLIC_PATHS = {
    "data/coverage.json",
    "data/corpus-report.json",
    "data/report.json",
    "data/schema.json",
    "summary-review.html",
}
FORBIDDEN_PUBLIC_FILENAMES = {"ASSET-AUDIT.json"}
FORBIDDEN_PAPER_FIELDS = {
    "provenance", "sourceRecoveryLocked", "abstractSource", "summaryEvidenceStatus",
}
FORBIDDEN_SUMMARY_FIELDS = {"model", "sourceAbstractSha256", "source"}
FORBIDDEN_TOPIC_FIELDS = {"provenance", "curationStatus"}
LEGAL_FILES = {"LICENSE", "NOTICE", "ThirdPartyNotices.txt"}
STATIC_RUNTIME_FILES = {"index.html", "sw.js", "favicon.svg"}
STATIC_DATA_FILES = set(DATA_FILES) | {"data/glossary.json"}
WASM_FILE = re.compile(r"wasm/ort-wasm-[A-Za-z0-9_.-]+\.(?:wasm|mjs)$")
ASSET_FILE = re.compile(r"assets/[A-Za-z0-9_.-]+\.(?:js|css|wasm)$")
PAPER_SHARD = re.compile(r"papers/[0-9a-f]{2}\.json$")
VECTOR_SHARD = re.compile(r"vectors/[0-9]{4}\.i8$")
TOPIC_PAPER_FILE = re.compile(r"topic-papers/[a-z0-9][a-z0-9-]*\.json$")


def error(errors: list[str], message: str) -> None:
    errors.append(message)


def valid_outbound_paper_url(value: object) -> bool:
    """Paper sources may be PDFs; only hosting a PDF is prohibited."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def valid_optional_outbound_url(value: object) -> bool:
    """Optional reader-facing links must either be blank or safe absolute URLs."""
    return value is None or value == "" or valid_outbound_paper_url(value)


def validate_hosted_files(root: Path, files: list[Path], errors: list[str]) -> int:
    """Enforce Pages asset limits and reject PDFs stored in the artifact."""
    total = sum(path.stat().st_size for path in files)
    for path in files:
        relative = path.relative_to(root)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            error(errors, f"file exceeds 100 MB: {relative} ({size} bytes)")
        if path.suffix.lower() == ".pdf":
            error(errors, f"PDF found in static artifact: {relative}")
        if path.suffix.lower() == ".map":
            error(errors, f"source map found in static artifact: {relative}")
        if relative.as_posix() in FORBIDDEN_PUBLIC_PATHS or path.name in FORBIDDEN_PUBLIC_FILENAMES:
            error(errors, f"internal review or operational artifact found in static bundle: {relative}")
        if path.suffix.lower() in TEXT_ASSET_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                error(errors, f"non-UTF-8 text asset: {relative}")
                continue
            for url in REMOTE_ASSET.findall(text):
                if url.lower().split("?", 1)[0].endswith(EXTERNAL_ASSET_SUFFIXES):
                    error(errors, f"external generated asset URL in {relative}: {url}")
    return total


def require_json(path: Path, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        error(errors, f"invalid JSON: {path}: {exc}")
        return None


def _safe_dynamic_path(value: object, pattern: re.Pattern[str]) -> str | None:
    """Accept only a normalized data-relative path with the expected shard name."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    return value


def validate_public_paths(root: Path, files: list[Path], manifest: dict[str, Any], errors: list[str]) -> None:
    """Allow only reader runtime assets and manifest-declared static data.

    This is deliberately an allowlist. A stale progress log or review page is
    still a privacy boundary failure even if no route links to it.
    """
    vectors = require_json(root / "data" / "vectors.json", errors)
    allowed = set(STATIC_RUNTIME_FILES) | set(MODEL_FILES) | STATIC_DATA_FILES | LEGAL_FILES
    shards = manifest.get("shards", {}) if isinstance(manifest, dict) else {}
    paper_shards = shards.get("papers", {}) if isinstance(shards, dict) else {}
    if not isinstance(paper_shards, dict):
        error(errors, "manifest paper shards must be an object for static allowlist validation")
    else:
        for prefix, relative in paper_shards.items():
            safe = _safe_dynamic_path(relative, PAPER_SHARD)
            if safe is None or prefix != Path(safe).stem:
                error(errors, f"manifest has invalid paper shard allowlist entry: {prefix!r}")
            else:
                allowed.add(f"data/{safe}")
    if not isinstance(vectors, dict) or not isinstance(vectors.get("shards"), list):
        error(errors, "vectors must provide shard metadata for static allowlist validation")
    else:
        for shard in vectors["shards"]:
            safe = _safe_dynamic_path(shard.get("path") if isinstance(shard, dict) else None, VECTOR_SHARD)
            if safe is None:
                error(errors, "vectors has invalid shard allowlist entry")
            else:
                allowed.add(f"data/{safe}")
    topics = require_json(root / "data" / "topics.json", errors)
    if not isinstance(topics, list):
        error(errors, "topics must be available for topic-paper static allowlist validation")
    else:
        for topic in topics:
            slug = topic.get("slug") if isinstance(topic, dict) else None
            safe = _safe_dynamic_path(f"topic-papers/{slug}.json" if isinstance(slug, str) else None, TOPIC_PAPER_FILE)
            if safe is None:
                error(errors, "topics has invalid slug for topic-paper allowlist")
            else:
                allowed.add(f"data/{safe}")
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in allowed or WASM_FILE.fullmatch(relative) or ASSET_FILE.fullmatch(relative):
            continue
        error(errors, f"unexpected static artifact outside reader allowlist: {relative}")


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_topic_graph(root: Path, topics: list[dict], errors: list[str]) -> None:
    """Verify each precomputed neighborhood is grounded in sanitized topic links."""
    graph = require_json(root / "data" / "topic-graph.json", errors)
    if not isinstance(graph, dict):
        return
    if set(graph) != {"version", "layoutKind", "defaultTopicSlug", "bounds", "cardDimensions", "topics", "neighborhoods"}:
        error(errors, "topic graph exposes unsupported or operational fields")
    rows, neighborhoods, bounds, cards = (
        graph.get("topics"), graph.get("neighborhoods"), graph.get("bounds"), graph.get("cardDimensions"),
    )
    if graph.get("version") != 1 or graph.get("layoutKind") != "focused-semantic-neighborhoods":
        error(errors, "topic graph has unsupported version or layout kind")
    if not isinstance(rows, list) or not isinstance(neighborhoods, list) or not isinstance(bounds, dict) or not isinstance(cards, dict):
        error(errors, "topic graph must contain topics, neighborhoods, bounds, and card dimensions")
        return
    if set(bounds) != {"width", "height"} or not all(_finite_number(bounds.get(key)) and bounds[key] > 0 for key in ("width", "height")):
        error(errors, "topic graph bounds must be positive finite geometry")
        return
    if set(cards) != {"root", "neighbor"} or any(
        not isinstance(card, dict)
        or set(card) != {"width", "height"}
        or not all(_finite_number(card.get(key)) and card[key] > 0 for key in ("width", "height"))
        for card in cards.values()
    ):
        error(errors, "topic graph card dimensions must be positive width/height pairs")
        return
    source_index = {topic.get("slug"): index for index, topic in enumerate(topics)}
    if len(rows) != len(topics) or len(neighborhoods) != len(topics):
        error(errors, "topic graph counts do not match sanitized topics")
        return
    # Length equality was checked above; avoid ``zip(strict=True)`` because the
    # GitHub workflow's system Python may be 3.9.
    for index, (source, row) in enumerate(zip(topics, rows)):
        expected_related = [source_index[slug] for slug in source.get("relatedTopics", []) if slug in source_index]
        if (
            not isinstance(row, dict)
            or set(row) != {"slug", "title", "area", "paperCount", "overview", "relatedTopics"}
            or not isinstance(row.get("overview"), str)
            or len(row["overview"]) > 240
            or row.get("slug") != source.get("slug")
            or row.get("title") != source.get("title")
            or row.get("area") != source.get("area")
            or row.get("paperCount") != len(source.get("paperSlugs") or [])
            or row.get("relatedTopics") != expected_related
        ):
            error(errors, f"topic graph topic is stale or inconsistent: {index}")
    if graph.get("defaultTopicSlug") not in source_index:
        error(errors, "topic graph default topic is absent from sanitized topics")
    related = [row.get("relatedTopics", []) if isinstance(row, dict) else [] for row in rows]
    incoming = [[] for _ in rows]
    for source, targets in enumerate(related):
        if not isinstance(targets, list) or any(not isinstance(target, int) or target < 0 or target >= len(rows) for target in targets):
            error(errors, f"topic graph has invalid related topic indices: {source}")
            continue
        for target in targets:
            incoming[target].append(source)
    def adjacent(index: int) -> set[int]:
        return set(related[index]) | set(incoming[index])
    for root, neighborhood in enumerate(neighborhoods):
        if not isinstance(neighborhood, dict) or set(neighborhood) != {"rootTopic", "nodes", "edges", "directNeighborTotal", "secondDegreeTotal"} or neighborhood.get("rootTopic") != root:
            error(errors, f"topic graph neighborhood has wrong root: {root}")
            continue
        direct = related[root][:5]
        root_adjacent = adjacent(root)
        candidate_scores: dict[int, tuple[int, int, str]] = {}
        for neighbor in direct:
            for candidate in adjacent(neighbor):
                if candidate == root or candidate in direct or candidate in root_adjacent:
                    continue
                rank = related[neighbor].index(candidate) if candidate in related[neighbor] else 10_000 + candidate
                connections, old_rank, _ = candidate_scores.get(candidate, (0, rank, rows[candidate].get("slug", "")))
                candidate_scores[candidate] = (connections + 1, min(old_rank, rank), rows[candidate].get("slug", ""))
        second_all = sorted(candidate_scores, key=lambda item: (-candidate_scores[item][0], candidate_scores[item][1], candidate_scores[item][2]))
        second = second_all[:6]
        expected_nodes = [root, *direct, *second]
        nodes = neighborhood.get("nodes")
        if not isinstance(nodes, list) or [node.get("topic") for node in nodes if isinstance(node, dict)] != expected_nodes or [node.get("degree") for node in nodes if isinstance(node, dict)] != [0, *([1] * len(direct)), *([2] * len(second))]:
            error(errors, f"topic graph neighborhood nodes are stale or inconsistent: {root}")
            continue
        if neighborhood.get("directNeighborTotal") != len(root_adjacent) or neighborhood.get("secondDegreeTotal") != len(second_all):
            error(errors, f"topic graph neighborhood totals are stale: {root}")
        visible = set(expected_nodes); expected_edges: set[tuple[int, int, int]] = set()
        for source in visible:
            for target in visible:
                if source >= target or target not in adjacent(source):
                    continue
                other = target if source == root else source
                degree = 1 if root in {source, target} and other in direct else 2
                expected_edges.add((source, target, degree))
        actual_edges = neighborhood.get("edges")
        actual = {(edge.get("source"), edge.get("target"), edge.get("degree")) for edge in actual_edges if isinstance(edge, dict)} if isinstance(actual_edges, list) else set()
        if actual != expected_edges or len(actual) != len(actual_edges or []) or any(set(edge) != {"source", "target", "degree"} for edge in actual_edges or [] if isinstance(edge, dict)):
            error(errors, f"topic graph neighborhood edges are stale or inconsistent: {root}")
        rectangles: list[tuple[float, float, float, float]] = []
        for node in nodes:
            if not isinstance(node, dict) or set(node) != {"topic", "degree", "x", "y"} or not all(_finite_number(node.get(key)) for key in ("x", "y")):
                error(errors, f"topic graph node has invalid geometry: {root}")
                continue
            card = cards["root" if node.get("degree") == 0 else "neighbor"]
            rectangle = (node["x"], node["y"], card["width"], card["height"])
            if rectangle[0] < 0 or rectangle[1] < 0 or rectangle[0] + rectangle[2] > bounds["width"] or rectangle[1] + rectangle[3] > bounds["height"]:
                error(errors, f"topic graph card falls outside bounds: {root}")
            for other in rectangles:
                if rectangle[0] < other[0] + other[2] and other[0] < rectangle[0] + rectangle[2] and rectangle[1] < other[1] + other[3] and other[1] < rectangle[1] + rectangle[3]:
                    error(errors, f"topic graph cards overlap: {root}")
            rectangles.append(rectangle)


def validate_topic_paper_lists(root: Path, topics: list[dict], papers: list[dict], errors: list[str]) -> None:
    """Ensure every complete topic list is a faithful compact projection."""
    paper_by_slug = {paper.get("slug"): paper for paper in papers if isinstance(paper.get("slug"), str)}
    required_topic_fields = {
        "slug", "title", "area", "overview", "whyItMatters", "evolution", "openQuestions", "difficulty", "paperCount",
    }
    for topic in topics:
        slug = topic.get("slug") if isinstance(topic, dict) else None
        if not isinstance(slug, str) or not TOPIC_PAPER_FILE.fullmatch(f"topic-papers/{slug}.json"):
            error(errors, "topic-paper list has invalid topic slug")
            continue
        value = require_json(root / "data" / "topic-papers" / f"{slug}.json", errors)
        if not isinstance(value, dict) or set(value) != {"version", "topic", "relatedTopics", "prerequisites", "papers"} or value.get("version") != 1:
            error(errors, f"topic-paper list has invalid schema: {slug}")
            continue
        context = value.get("topic")
        if not isinstance(context, dict) or set(context) != required_topic_fields:
            error(errors, f"topic-paper list exposes unsupported topic fields: {slug}")
            continue
        expected_context = {field: topic.get(field) for field in required_topic_fields if field != "paperCount"}
        expected_context["paperCount"] = len(topic.get("paperSlugs") or [])
        if context != expected_context:
            error(errors, f"topic-paper context is stale or inconsistent: {slug}")
        topic_lookup = {item.get("slug"): item for item in topics if isinstance(item, dict)}
        expected_related = [
            {"slug": related_slug, "title": topic_lookup[related_slug].get("title"), "paperCount": len(topic_lookup[related_slug].get("paperSlugs") or [])}
            for related_slug in topic.get("relatedTopics") or [] if related_slug in topic_lookup
        ]
        expected_prerequisites = [
            {"slug": prerequisite_slug, "title": topic_lookup[prerequisite_slug].get("title")}
            for prerequisite_slug in topic.get("prerequisites") or [] if prerequisite_slug in topic_lookup
        ]
        if len(expected_related) != len(topic.get("relatedTopics") or []) or value.get("relatedTopics") != expected_related:
            error(errors, f"topic-paper related topics are stale or inconsistent: {slug}")
        if len(expected_prerequisites) != len(topic.get("prerequisites") or []) or value.get("prerequisites") != expected_prerequisites:
            error(errors, f"topic-paper prerequisites are stale or inconsistent: {slug}")
        rows = value.get("papers")
        expected_slugs = topic.get("paperSlugs") or []
        if not isinstance(rows, list) or [row.get("slug") for row in rows if isinstance(row, dict)] != expected_slugs or len(rows) != len(set(expected_slugs)):
            error(errors, f"topic-paper membership is incomplete, duplicated, or out of order: {slug}")
            continue
        for row in rows:
            if not isinstance(row, dict) or not set(row).issubset({"slug", "title", "authors", "year", "venue", "sourceUrl", "sourceLinkLabel", "tldr"}) or not {"slug", "title", "authors", "year", "venue", "sourceUrl", "sourceLinkLabel"}.issubset(row):
                error(errors, f"topic-paper row exposes unsupported fields: {slug}")
                continue
            source = paper_by_slug.get(row.get("slug"))
            if not source:
                error(errors, f"topic-paper row has missing paper: {slug}")
                continue
            expected_url = source.get("originalUrl") or source.get("url")
            expected = {
                "slug": source.get("slug"), "title": source.get("title"), "authors": source.get("authors"),
                "year": source.get("year"), "venue": source.get("venue"), "sourceUrl": expected_url,
                "sourceLinkLabel": source.get("sourceLinkLabel"),
            }
            if isinstance((source.get("summary") or {}).get("tldr"), str) and source["summary"]["tldr"]:
                expected["tldr"] = source["summary"]["tldr"]
            if row != expected or not valid_outbound_paper_url(row.get("sourceUrl")):
                error(errors, f"topic-paper row is stale or has invalid source metadata: {slug}/{row.get('slug')}")


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
        restricted = FORBIDDEN_PAPER_FIELDS.intersection(paper)
        if restricted:
            error(errors, f"paper {paper.get('slug')} exposes internal fields: {sorted(restricted)}")
        if not valid_outbound_paper_url(paper.get("url")):
            error(errors, f"paper {paper.get('slug')} has no valid absolute HTTP(S) original URL")
        for field in ("originalUrl", "codeUrl", "projectUrl"):
            if not valid_optional_outbound_url(paper.get(field)):
                error(errors, f"paper {paper.get('slug')} has invalid {field}; expected blank or absolute HTTP(S) URL")
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
        if paper.get("summary") is not None:
            if not isinstance(paper["summary"], dict):
                error(errors, f"paper {paper.get('slug')} has invalid summary")
            else:
                restricted_summary = FORBIDDEN_SUMMARY_FIELDS.intersection(paper["summary"])
                if restricted_summary:
                    error(errors, f"paper {paper.get('slug')} summary exposes internal fields: {sorted(restricted_summary)}")
            summaries += 1
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        restricted = FORBIDDEN_TOPIC_FIELDS.intersection(topic)
        if restricted:
            error(errors, f"topic {topic.get('slug')} exposes internal fields: {sorted(restricted)}")
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
    total = validate_hosted_files(root, files, errors)
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
    for filename in ("search.json", "topics.json", "topic-graph.json", "vectors.json"):
        path = data_root / filename
        if path.is_file():
            parsed = require_json(path, errors)
            if has_remote_asset(parsed):
                error(errors, f"external generated asset URL in data/{filename}")
    if not isinstance(manifest, dict) or not manifest.get("snapshot"):
        error(errors, "manifest must declare snapshot")
    elif {"coverage", "provenance", "report"}.intersection(manifest):
        error(errors, "manifest exposes internal audit metadata")
    if isinstance(manifest, dict):
        validate_public_paths(root, files, manifest, errors)
        papers, topics, summaries = validate_corpus(root, manifest, errors)
        validate_topic_graph(root, topics, errors)
        validate_topic_paper_lists(root, topics, papers, errors)
    else:
        _papers, topics, summaries = [], [], 0
    if args.expected_summaries is not None and summaries != args.expected_summaries:
        error(errors, f"expected {args.expected_summaries} summaries; found {summaries}")
    if errors:
        print("Static artifact validation failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in errors), file=sys.stderr)
        return 1
    print(json.dumps({"artifact": str(root), "files": len(files), "siteBytes": total}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
