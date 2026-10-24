#!/usr/bin/env python3
"""Measure a built static artifact and write honest release/provenance reports."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 100_000_000
MAX_SITE_BYTES = 1_000_000_000
ABSTRACT_PLACEHOLDERS = {
    "abstract unavailable", "abstract not available", "unavailable", "none", "n/a",
}


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def size_of(root: Path) -> int:
    return sum(path.stat().st_size for path in artifact_files(root))


def paper_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shard in (manifest.get("shards", {}).get("papers", {}) or {}).values():
        value = read_json(root / "data" / str(shard), [])
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def venue_year_counts(papers: list[dict[str, Any]]) -> dict[str, int]:
    """Return stable reader-corpus counts for the internal release report."""
    return dict(sorted(
        Counter(f"{paper.get('venue', 'unknown')}:{paper.get('year', 'unknown')}" for paper in papers).items()
    ))


def summary_eligibility_reason(abstract: str, abstract_quality: str = "") -> str | None:
    """Mirror the export evidence gate without requiring pipeline dependencies."""
    if abstract_quality == "official-landing-page-possibly-full-text":
        return "abstract quality requires source cleanup"
    normalized = abstract.casefold().strip()
    if not abstract.strip() or normalized in ABSTRACT_PLACEHOLDERS:
        return "official abstract unavailable"
    if len(re.findall(r"\b[\w’'-]+\b", abstract)) < 40:
        return "insufficient abstract evidence (fewer than 40 words)"
    return None


def summary_coverage(papers: list[dict[str, Any]]) -> dict[str, int]:
    """Classify public records with the same evidence gate used at export time."""
    counts = {"narrative": 0, "legacy": 0, "pending": 0, "unavailable": 0}
    for paper in papers:
        summary = paper.get("summary")
        if isinstance(summary, dict):
            counts["narrative" if summary.get("paragraphs") else "legacy"] += 1
        elif summary_eligibility_reason(
            str(paper.get("abstract") or ""), str(paper.get("abstractQuality") or "")
        ):
            counts["unavailable"] += 1
        else:
            counts["pending"] += 1
    return counts


def _usage_totals(value: Any) -> dict[str, int]:
    totals = {"inputTokens": 0, "outputTokens": 0}
    entries = value.get("reportedUsage", []) if isinstance(value, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return totals
    for entry in entries:
        usage = entry.get("usage", entry) if isinstance(entry, dict) else {}
        if not isinstance(usage, dict):
            continue
        for output, source in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
            amount = usage.get(source)
            if isinstance(amount, int):
                totals[output] += amount
    return totals


def _runner_run(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    batches = manifest.get("batches", []) if isinstance(manifest, dict) else []
    complete = manifest.get("completeRecords")
    if not isinstance(complete, int) and isinstance(batches, list):
        complete = sum(
            int(batch.get("records", 0))
            for batch in batches
            if isinstance(batch, dict) and batch.get("status") in {"complete", "skipped"}
        )
    eligible = manifest.get("eligibleRecords") if isinstance(manifest, dict) else None
    failed = manifest.get("failedBatches") if isinstance(manifest, dict) else None
    if isinstance(failed, int) and failed:
        state = "failed"
    elif isinstance(eligible, int) and complete == eligible:
        state = "complete"
    else:
        state = "running-or-incomplete"
    return {
        "name": name,
        "model": manifest.get("model") if isinstance(manifest, dict) else None,
        "state": state,
        "completeRecords": complete or 0,
        "eligibleRecords": eligible,
        "failedBatches": failed,
        "actualReportedTokens": _usage_totals(manifest),
        "estimatedTokens": {
            "input": manifest.get("estimatedInputTokens"),
            "output": manifest.get("estimatedOutputTokens"),
        } if isinstance(manifest, dict) else {},
        "dollarCost": manifest.get("dollarCost", "unknown") if isinstance(manifest, dict) else "unknown",
    }


def provenance_report(repo: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    narratives = repo / "data" / "generated" / "narratives"
    manual = sorted(path for path in narratives.glob("*.json") if path.name.startswith(("a-", "b-")))
    manual_records = sum(len(read_json(path, {})) for path in manual)
    runs: list[dict[str, Any]] = [{
        "name": "human-directed Astra review sample",
        "model": "gpt-6-astra",
        "completeRecords": manual_records,
        "actualReportedTokens": "not measured for this interactive authoring pass",
        "dollarCost": "unknown",
    }]
    pilot = read_json(narratives / "working" / "pilot-full-00000.manifest.json", {})
    if pilot:
        runs.append(_runner_run("CLI pilot", pilot))
    for path in sorted(narratives.glob("*/working/manifest.json")):
        runs.append(_runner_run(path.parent.parent.name, read_json(path, {})))
    return {
        "summaryModel": "gpt-6-astra",
        "source": "official abstract",
        "narrativeContract": "one TLDR plus 3–5 explanatory paragraphs; the unchanged source abstract renders first",
        "renderedArtifactCoverage": corpus.get("summaryCoverage", {}),
        "authoringRuns": runs,
        "accountDollarCost": "unknown; local Codex account usage is not an API price quote",
        "notes": [
            "Authoring-run counts are not equivalent to summaries rendered in this artifact: source-hash validation excludes stale or superseded outputs.",
            "Estimated tokens are planning values; actual reported tokens are kept separately when the CLI reports them.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="site/dist")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = Path(args.directory).resolve()
    data = root / "data"
    manifest = read_json(data / "manifest.json", {})
    topics = read_json(data / "topics.json", [])
    coverage = read_json(repo / "pipeline" / "data" / "raw" / "coverage.json", {})
    model = read_json(repo / "pipeline" / "model-audits" / "Xenova" / "all-MiniLM-L6-v2" / "ASSET-AUDIT.json", {})
    destination = repo / "docs" / "build-report.json"
    papers = paper_rows(root, manifest)
    files = artifact_files(root)
    largest = max(files, key=lambda path: path.stat().st_size) if files else None
    citation_matches = sum(paper.get("citationCount") is not None for paper in papers)
    venue_totals = dict(sorted(Counter(str(paper.get("venue", "unknown")) for paper in papers).items()))
    rendered_summary_coverage = summary_coverage(papers)
    corpus = {
        "papersByVenueYear": venue_year_counts(papers),
        "curatedTopics": len(topics),
        "summaryCoverage": rendered_summary_coverage,
        "citationMatchRate": round(citation_matches / len(papers), 4) if papers else 0,
    }
    report = {
        "snapshot": manifest.get("snapshot"),
        "scope": "Static corpus snapshot; coverage and incomplete curation states are reported explicitly.",
        # This release-review report intentionally lives outside the browser
        # artifact. Readers need the atlas, not operational build metadata.
        "siteBytes": 0,
        "papers": manifest.get("totalPapers", len(papers)),
        "topics": manifest.get("totalTopics", len(topics)),
        "summaries": sum(paper.get("summary") is not None for paper in papers),
        "coverage": coverage,
        "artifact": {
            "files": len(files), "bytes": 0, "megabytesDecimal": 0,
            "largestFile": {"path": str(largest.relative_to(root)) if largest else None, "bytes": largest.stat().st_size if largest else 0},
            "limits": {"maxFileBytes": MAX_FILE_BYTES, "maxSiteBytes": MAX_SITE_BYTES},
            "withinGitHubPagesLimits": False,
        },
        "corpus": {
            "papers": manifest.get("totalPapers", len(papers)),
            "papersByVenue": venue_totals,
            "papersByVenueYear": corpus["papersByVenueYear"],
            "topics": manifest.get("totalTopics", len(topics)),
            "curatedTopics": corpus.get("curatedTopics", 0),
            "summaryCoverage": corpus.get("summaryCoverage", {}),
            "citationMatches": citation_matches,
            "citationMatchRate": corpus.get("citationMatchRate"),
        },
        "model": {key: model.get(key) for key in ("model", "revision", "runtime", "source")},
        "knownCoverageGaps": "ICML 1980 and 1985 original workshop inventories remain unverified; see data/generated/earliest-proceedings-audit.json.",
        "validation": {"artifactMeasured": True, "deployment": "pending"},
    }
    for _ in range(2):
        report["artifact"]["bytes"] = size_of(root)
        report["siteBytes"] = report["artifact"]["bytes"]
        report["artifact"]["megabytesDecimal"] = round(report["artifact"]["bytes"] / 1_000_000, 2)
        report["artifact"]["withinGitHubPagesLimits"] = report["artifact"]["bytes"] <= MAX_SITE_BYTES and all(path.stat().st_size <= MAX_FILE_BYTES for path in files if path != destination)
        write_json(destination, report)
    report["artifact"]["bytes"] = size_of(root)
    report["siteBytes"] = report["artifact"]["bytes"]
    report["artifact"]["megabytesDecimal"] = round(report["artifact"]["bytes"] / 1_000_000, 2)
    report["artifact"]["withinGitHubPagesLimits"] = report["artifact"]["bytes"] <= MAX_SITE_BYTES and all(path.stat().st_size <= MAX_FILE_BYTES for path in artifact_files(root))
    write_json(destination, report)
    write_json(repo / "data" / "generated" / "summary-provenance-report.json", provenance_report(repo, corpus))
    print(json.dumps({"report": str(destination), "siteBytes": report["artifact"]["bytes"], "provenance": "data/generated/summary-provenance-report.json"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
