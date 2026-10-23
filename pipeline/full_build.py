"""Truthful, resumable orchestration for the static full-corpus release.

This module deliberately separates mechanical preparation from editorial
completion: it never manufactures a topic or paper summary when an Astra shard
is absent. It is intended for one-time offline invocation, not the Pages build.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .corpus import meta_matches, select, verify_meta
from .export import export_site_data
from .glossary import glossary
from .settings import Settings
from .similarity import similar
from .summaries import import_summaries
from .summaries import valid_astra_narrative
from .util import read_json, write_json


def _state_path(settings: Settings) -> Path:
    return settings.derived_dir / "full-build-status.json"


def freeze_summary_input(settings: Settings) -> Path:
    """Freeze current paper/topic evidence before authoring; never use title-only rows."""
    papers, fingerprint = select(settings, "full")
    source = settings.derived_dir / "papers-topics-full.json"
    if source.exists():
        verify_meta(source, "full", fingerprint, "topic papers")
        papers = read_json(source, papers)
    rows = [{"paperSlug": p["slug"], "title": p["title"], "abstract": p.get("abstract", ""),
             "topicSlugs": p.get("topicSlugs", []), "abstractQuality": p.get("abstractQuality", "")}
            for p in papers]
    path = settings.derived_dir / "full-paper-summary-input.json"
    write_json(path, rows)
    return path


def freeze_existing_exclusions(settings: Settings, input_path: Path) -> Path:
    """Freeze only summaries whose Astra evidence still matches this corpus.

    The authoring output lives below ``narratives/``. Passing that parent as a
    live skip directory would make a restart re-evaluate its own partial shards,
    so this immutable slug list is created before the first full run instead.
    """
    from .astra_runner import load_records, valid_existing_summary

    # Only evidence currently eligible for new Astra work can be excluded from
    # the run. A quarantined source is instead counted as unavailable, even if
    # an older summary happens to share its current text hash.
    records, _ = load_records(input_path, None, None)
    by_slug = {record.slug: record for record in records}
    narrative_root = settings.root / "data/generated/narratives"
    eligible: set[str] = set()
    if narrative_root.exists():
        # The runner's output is deliberately excluded: it has its own frozen
        # manifest and valid batches are resumed by their original boundaries.
        for shard in sorted(narrative_root.rglob("*.json")):
            relative = shard.relative_to(narrative_root)
            if {"full", "working", "archive"} & set(relative.parts):
                continue
            rows = read_json(shard, {})
            if not isinstance(rows, dict):
                continue
            for slug, summary in rows.items():
                record = by_slug.get(str(slug))
                if record is None:
                    continue
                if valid_existing_summary(record, summary):
                    eligible.add(str(slug))
    path = settings.derived_dir / "full-summary-exclusions.json"
    write_json(
        path,
        {
            "inputHash": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "slugs": sorted(eligible),
        },
    )
    return path


def full_status(settings: Settings) -> dict[str, Any]:
    """Report curation state without describing incomplete content as finished."""
    papers, fingerprint = select(settings, "full")
    papers_path = settings.derived_dir / "papers-topics-full.json"
    current = papers
    if papers_path.exists():
        verify_meta(papers_path, "full", fingerprint, "topic papers")
        current = read_json(papers_path, papers)
    frozen = freeze_summary_input(settings)
    from .astra_runner import load_records

    eligible, unavailable = load_records(frozen, None, None)
    eligible_slugs = {record.slug for record in eligible}

    def valid_narrative(paper: dict[str, Any]) -> bool:
        return paper["slug"] in eligible_slugs and valid_astra_narrative(paper)
    narrative = sum(valid_narrative(paper) for paper in current)
    legacy = sum(
        paper["slug"] in eligible_slugs
        and bool(paper.get("summary"))
        and not bool((paper.get("summary") or {}).get("paragraphs"))
        for paper in current
    )
    audit = read_json(settings.derived_dir / "summary-import-errors.json", {})
    topics = read_json(settings.derived_dir / "topics-full.json", [])
    topic_rows = [topic for topic in topics if isinstance(topic, dict)]
    pending_topics = sum(topic.get("curationStatus") != "complete" for topic in topic_rows)
    topics_ready = bool(topic_rows) and pending_topics == 0
    return {
        "papers": len(current),
        "eligiblePapers": len(eligible),
        "unavailablePapers": len(unavailable),
        "narrativeSummaries": narrative,
        "legacySummaries": legacy,
        "pendingSummaries": max(0, len(eligible) - narrative - legacy),
        "pendingEligibleSummaries": max(0, len(eligible) - narrative - legacy),
        "summaryImportErrors": len(audit) if isinstance(audit, dict) else None,
        "topicCurationPending": pending_topics,
        "topicsReady": topics_ready,
        "readyForRelease": narrative == len(current) and not audit and topics_ready,
    }


def _reusable_full_topology(settings: Settings) -> dict[str, Any] | None:
    """Return existing reviewed topology only when it still exactly fits input.

    Topic identities are editorially meaningful once guide curation begins.
    A deterministic KMeans invocation can still rename/reassign clusters after
    a controlled source correction, so matching metadata alone is insufficient:
    require both artifacts, source-order alignment, and a complete membership
    relation before deciding that reuse is safe.
    """
    source_papers, fingerprint = select(settings, "full")
    topics_path = settings.derived_dir / "topics-full.json"
    papers_path = settings.derived_dir / "papers-topics-full.json"
    if not (
        meta_matches(topics_path, "full", fingerprint)
        and meta_matches(papers_path, "full", fingerprint)
    ):
        return None
    topics = read_json(topics_path, [])
    papers = read_json(papers_path, [])
    if not isinstance(topics, list) or not isinstance(papers, list) or not topics:
        return None
    if [paper.get("slug") for paper in papers] != [paper.get("slug") for paper in source_papers]:
        return None
    topic_slugs = {topic.get("slug") for topic in topics if isinstance(topic, dict) and topic.get("slug")}
    if len(topic_slugs) != len(topics):
        return None
    assigned = set()
    for topic in topics:
        members = topic.get("paperSlugs") if isinstance(topic, dict) else None
        if not isinstance(members, list) or not members:
            return None
        assigned.update(members)
    source_slugs = {paper["slug"] for paper in source_papers}
    if assigned != source_slugs:
        return None
    if any(
        not isinstance(paper.get("topicSlugs"), list)
        or not paper["topicSlugs"]
        or not set(paper["topicSlugs"]).issubset(topic_slugs)
        for paper in papers
    ):
        return None
    broad_groups = {
        topic.get("broadGroupId") for topic in topics if isinstance(topic, dict) and topic.get("broadGroupId")
    }
    return {"topics": len(topics), "broadGroups": len(broad_groups), "reused": True}


def prepare_full(
    settings: Settings,
    *, chunk_size: int = 256, batch_size: int = 32,
) -> dict[str, Any]:
    """Run injected deterministic full stages and checkpoint a truthful status.

    The cache-aware embedding and 256/32 cluster implementation lives in their
    respective modules. Their output artifacts are independently resumable.
    """
    from .embedding import embed_corpus
    from .topics import cluster

    embedded = embed_corpus(settings, "full", chunk_size=chunk_size, batch_size=batch_size)
    clustered = _reusable_full_topology(settings)
    if clustered is None:
        clustered = cluster(settings, "full")
        clustered["reused"] = False
    status = full_status(settings)
    report = {
        "embedding": embedded,
        "clustering": clustered,
        "topologyReused": bool(clustered.get("reused")),
        "status": status,
    }
    write_json(_state_path(settings), report)
    return report


def finalize_full(settings: Settings, summary_file: Path | None = None, *, allow_incomplete: bool = False, build_site: bool = True) -> dict[str, Any]:
    """Import validated narratives, then run static-only finishing stages.

    Finalization stops before export if any summary fails validation or if any
    paper remains pending. This prevents a partial one-time curation run from
    being represented as a complete product snapshot.
    """
    # A stale derived single-file import must not hide durable Astra shards from
    # this one-time release finalization. The narrative root is source-hash
    # resolved by the directory importer, with working/archive trees excluded.
    summary_source = summary_file or settings.root / "data/generated/narratives"
    imported = import_summaries(settings, summary_source, "full")
    if imported["errors"]:
        raise RuntimeError(f"summary import rejected {len(imported['errors'])} paper(s)")
    from .topics import import_topics

    topic_import = import_topics(settings, "full")
    status = full_status(settings)
    if not status["readyForRelease"] and not allow_incomplete:
        raise RuntimeError(f"full curation pending for {status['pendingSummaries']} paper(s)")
    related = similar(settings, "full")
    terms = glossary(settings, "full")
    exported = export_site_data(settings, "full")
    result = {"summaries": imported, "topics": topic_import, "similarity": related, "glossary": terms, "export": exported, "status": full_status(settings), "allowIncomplete": allow_incomplete}
    if build_site:
        for command, cwd in ((["npm", "run", "build"], settings.root / "site"),
                             ([sys.executable, "scripts/write-artifact-report.py", "site/dist"], settings.root),
                             ([sys.executable, "scripts/validate-artifacts.py", "site/dist"], settings.root)):
            subprocess.run(command, cwd=cwd, check=True)
        result["siteBuilt"] = True
    write_json(_state_path(settings), result)
    return result


def generate_full(settings: Settings, *, workers: int = 4) -> dict[str, Any]:
    """Run the resumable authoring runner in the foreground with durable logs."""
    input_path = freeze_summary_input(settings)
    output = settings.root / "data/generated/narratives/full"
    working = output / "working"
    exclusions = settings.derived_dir / "full-summary-exclusions.json"
    # Once a run has a manifest, the runner owns its frozen selection and will
    # reject a changed input. Do not silently rewrite its exclusion evidence.
    has_run_manifest = (working / "manifest.json").exists()
    # Before the first full-run manifest exists, more reviewed/prelude shards
    # may finish while topic curation is underway. Refresh every launch so they
    # are not needlessly reauthored. Once authoring starts, preserve exactly the
    # frozen exclusion set for stable batch boundaries.
    if not has_run_manifest:
        exclusions = freeze_existing_exclusions(settings, input_path)
    working.mkdir(parents=True, exist_ok=True)
    log = working / "astra-runner.log"
    command = [
        sys.executable, "-m", "pipeline.astra_runner", "--input", str(input_path),
        "--output-dir", str(output), "--workers", str(workers),
        "--exclude-file", str(exclusions),
    ]
    state = {"generation": {"pid": None, "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "command": command, "log": str(log), "status": "running"}}
    write_json(_state_path(settings), state)
    with log.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(command, cwd=settings.root, stdout=handle, stderr=subprocess.STDOUT)
        state["generation"]["pid"] = process.pid
        write_json(_state_path(settings), state)
        code = process.wait()
    state["generation"].update({"finishedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "complete" if code == 0 else "failed", "exitCode": code})
    write_json(_state_path(settings), state)
    if code:
        raise RuntimeError(f"Astra runner failed; see {log}")
    return state


def run_full(settings: Settings, *, workers: int = 4, allow_incomplete: bool = False) -> dict[str, Any]:
    """Author, import, and statically rebuild in one resumable offline command."""
    generation = generate_full(settings, workers=workers)
    final = finalize_full(settings, allow_incomplete=allow_incomplete)
    return {"generation": generation, "finalization": final}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "status", "generate", "finalize", "run"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--no-site-build", action="store_true")
    args = parser.parse_args(argv)
    from .settings import load_settings
    settings = load_settings(Path.cwd(), args.config)
    if args.command == "prepare": result = prepare_full(settings)
    elif args.command == "status": result = full_status(settings)
    elif args.command == "generate": result = generate_full(settings, workers=args.workers)
    elif args.command == "finalize": result = finalize_full(settings, allow_incomplete=args.allow_incomplete, build_site=not args.no_site_build)
    else: result = run_full(settings, workers=args.workers, allow_incomplete=args.allow_incomplete)
    print(__import__("json").dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
