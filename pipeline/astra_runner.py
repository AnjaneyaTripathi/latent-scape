"""Resumable local Codex/Astra authoring runner for abstract-grounded narratives.

The runner feeds Codex only records supplied with ``--input``. It never fetches
papers or PDFs. Every accepted explanation is tied to the supplied official
abstract by SHA-256 before becoming an importable narrative shard.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .summaries import SUMMARY_SCHEMA, summary_eligibility_reason
from .util import read_json, write_json


MODEL = "gpt-6-astra"
MAX_WORKERS = 32
MAX_ATTEMPTS = 3
WORD_MIN = 120
WORD_MAX = 240
# One real, local Astra probe reported 17,955 input tokens for 208 tokens of
# supplied evidence. Include that fixed Codex context overhead in planning,
# while keeping estimates explicitly distinct from reported usage.
PROBE_CONTEXT_OVERHEAD_TOKENS = 17_750
VALIDATION_VERSION = 2


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunLock:
    """An advisory process lock for one mutable narrative output directory."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.handle.close()
            self.handle = None
            raise SystemExit(
                f"another Astra runner owns {self.path.parent}; stop or drain it before resuming"
            ) from error
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "startedAt": _now()}) + "\n")
        self.handle.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class RateLimitGate:
    """Coordinate bounded retry delays across concurrent batches."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._not_before = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self._not_before - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + seconds)


def _control_path(working_dir: Path) -> Path:
    return working_dir / "control.json"


def _target_workers(working_dir: Path, fallback: int) -> int:
    """Allow a live owner to increase/decrease scheduling without a new run."""
    control = read_json(_control_path(working_dir), {})
    target = control.get("targetWorkers") if isinstance(control, dict) else None
    return target if isinstance(target, int) and 1 <= target <= MAX_WORKERS else fallback


def disabled_configured_mcp_overrides(config_path: Path | None = None) -> list[str]:
    """Disable only configured MCP servers for evidence-only batch calls.

    The authoring prompt permits no tools and supplies all evidence itself.
    Reading the top-level server names avoids starting unrelated local MCP
    processes for every concurrent Codex batch. Configuration values (including
    credentials) are never returned or written to runner artifacts.
    """
    path = config_path or Path.home() / ".codex" / "config.toml"
    try:
        with path.open("rb") as handle:
            configured = tomllib.load(handle).get("mcp_servers", {})
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if not isinstance(configured, dict):
        return []
    overrides: list[str] = []
    for name in sorted(configured):
        # Codex's one-off dotted paths accept TOML bare keys. Quoting a path
        # segment would instead create a literal quoted server name. Names that
        # cannot be represented safely are left alone rather than guessed.
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name):
            overrides.extend(["-c", f"mcp_servers.{name}.enabled=false"])
    return overrides


def _write_progress(
    working_dir: Path,
    manifest: dict[str, Any],
    results: dict[int, dict[str, Any]],
    active: list[dict[str, Any]],
    started: float,
    *,
    baseline_complete: int,
    drain_requested: bool,
) -> None:
    finished = [item for item in results.values() if item.get("status") in {"complete", "skipped"}]
    complete = sum(int(item.get("records", 0)) for item in finished)
    active_records = sum(int(item.get("records", 0)) for item in active)
    elapsed = max(time.monotonic() - started, 1e-9)
    run_complete = max(0, complete - baseline_complete)
    rate = run_complete / (elapsed / 3600)
    remaining = max(0, int(manifest.get("eligibleRecords", 0)) - complete)
    write_json(
        working_dir / "progress.json",
        {
            "updatedAt": _now(),
            "model": MODEL,
            "configuredWorkers": manifest.get("workers"),
            "targetWorkers": _target_workers(working_dir, int(manifest.get("workers", 1))),
            "completedRecords": complete,
            "completedThisOwner": run_complete,
            "pendingRecords": max(0, remaining - active_records),
            "activeRecords": active_records,
            "activeBatches": active,
            "failedBatches": sum(item.get("status") == "failed" for item in results.values()),
            "measuredPapersPerHour": round(rate, 2),
            "estimatedRemainingHoursAtCurrentRate": round(remaining / rate, 2) if rate else None,
            "drainRequested": drain_requested,
        },
    )


@dataclass(frozen=True)
class Record:
    slug: str
    title: str
    abstract: str
    source_hash: str
    topic_slugs: tuple[str, ...]
    abstract_quality: str


def abstract_hash(abstract: str) -> str:
    return hashlib.sha256(abstract.encode("utf-8")).hexdigest()


def normalize_record(value: dict[str, Any]) -> Record:
    slug = str(value.get("paperSlug") or value.get("slug") or "").strip()
    title = str(value.get("title") or "").strip()
    abstract = str(value.get("abstract") or "")
    if not slug or not title:
        raise ValueError("each input record needs paperSlug/slug and title")
    calculated = abstract_hash(abstract)
    supplied = value.get("sourceAbstractSha256")
    if supplied and supplied != calculated:
        raise ValueError(f"input hash does not match abstract for {slug}")
    return Record(slug, title, abstract, calculated, tuple(str(item) for item in value.get("topicSlugs", []) if str(item)), str(value.get("abstractQuality") or ""))


def load_exclusions(path: Path) -> set[str]:
    if path.suffix.lower() != ".json":
        return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    value = read_json(path)
    if isinstance(value, dict):
        if isinstance(value.get("slugs"), list):
            return {str(slug) for slug in value["slugs"] if isinstance(slug, str)}
        return {str(key) for key in value}
    if isinstance(value, list):
        slugs: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                slug = item.get("paperSlug") or item.get("slug")
                if slug:
                    slugs.add(str(slug))
            elif isinstance(item, str):
                slugs.add(item)
            else:
                raise ValueError("--exclude-file JSON lists must contain slugs or records with paperSlug/slug")
        return slugs
    raise ValueError("--exclude-file JSON must be an object or list of paper slugs")


def valid_existing_summary(record: Record, summary: object) -> bool:
    """Check reusable output with the same topic cleanup as import.

    Early reviewed summaries can mention retired human labels.  They remain
    abstract-hash-valid Astra work; import drops unknown background labels, so
    reuse must not regenerate them merely because those labels are no longer
    cluster slugs.
    """
    if not isinstance(summary, dict):
        return False
    candidate = dict(summary)
    background = candidate.get("backgroundConcepts")
    if isinstance(background, list):
        candidate["backgroundConcepts"] = [
            topic for topic in background if isinstance(topic, str) and topic in record.topic_slugs
        ]
    accepted, errors = validate_batch([record], {record.slug: candidate})
    return not errors and bool(accepted)


def existing_shard_slugs(path: Path, records: dict[str, Record]) -> set[str]:
    """Read only existing summaries that remain valid for the current abstract."""
    if not path.exists() or not path.is_dir():
        raise ValueError("--skip-existing-dir must name an existing directory")
    slugs: set[str] = set()
    for shard in path.rglob("*.json"):
        if {"working", "archive"} & set(shard.parts):
            continue
        rows = read_json(shard, {})
        if isinstance(rows, dict) and all(isinstance(summary, dict) and "tldr" in summary for summary in rows.values()):
            for slug, summary in rows.items():
                record = records.get(str(slug))
                if not record:
                    continue
                if valid_existing_summary(record, summary):
                    slugs.add(str(slug))
    return slugs


def load_records(path: Path, limit: int | None, exclude_file: Path | None, skip_existing_dir: Path | None = None) -> tuple[list[Record], list[dict[str, str]]]:
    raw = read_json(path)
    if isinstance(raw, dict) and isinstance(raw.get("papers"), list):
        raw = raw["papers"]
    if not isinstance(raw, list):
        raise ValueError("--input must be a JSON list of paper records or narrative prompts")
    explicit_excluded = load_exclusions(exclude_file) if exclude_file else set()
    records: list[Record] = []
    unavailable: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("input list contains a non-object record")
        record = normalize_record(item)
        if record.slug in seen:
            raise ValueError(f"duplicate input slug: {record.slug}")
        seen.add(record.slug)
        if record.slug in explicit_excluded:
            unavailable.append({"slug": record.slug, "reason": "excluded"})
        elif reason := summary_eligibility_reason(record.abstract, record.abstract_quality):
            unavailable.append({"slug": record.slug, "reason": reason})
        else:
            records.append(record)
    if skip_existing_dir:
        existing = existing_shard_slugs(skip_existing_dir, {record.slug: record for record in records})
        kept: list[Record] = []
        for record in records:
            if record.slug in existing:
                unavailable.append({"slug": record.slug, "reason": "already summarized with matching abstract hash"})
            else:
                kept.append(record)
        records = kept
    return (records if limit is None else records[:limit]), unavailable


def narrative_schema() -> dict[str, Any]:
    """Strict Codex output schema: only the new, abstract-bound narrative shape."""
    properties = SUMMARY_SCHEMA["properties"]
    required = ["tldr", "paragraphs", "difficulty", "source", "model", "backgroundConcepts", "sourceAbstractSha256"]
    return {"type": "object", "properties": {key: properties[key] for key in required}, "required": required, "additionalProperties": False}


def batch_schema(records: list[Record]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {record.slug: narrative_schema() for record in records},
        "required": [record.slug for record in records],
        "additionalProperties": False,
    }


def prompt_for(records: list[Record], retry_errors: dict[str, list[str]] | None = None) -> str:
    evidence = [{
        "paperSlug": record.slug,
        "title": record.title,
        "officialAbstract": record.abstract,
        "sourceAbstractSha256": record.source_hash,
        "allowedBackgroundConcepts": list(record.topic_slugs),
    } for record in records]
    return f"""You are curating research-paper explanations for Latentscape. Return one JSON object keyed exactly by the supplied paperSlug values.

Use only title, officialAbstract, sourceAbstractSha256, and allowedBackgroundConcepts in the supplied evidence. Do not retrieve sources, run commands, use web access, or invent experiments, metrics, baselines, dates, citations, or limitations.

For each key return: tldr (one concise sentence); paragraphs (3–5 substantive plain-language paragraphs, 140–240 words total, focused on what the paper did, why its approach matters, and what its stated findings mean). Preserve the strength of every claim: never turn a sufficient, conditional, possible, empirical, or qualified statement into a necessary, universal, or certain one. Mention a real stated assumption or limit only when the supplied abstract supports it; do not devote a paragraph to generic missing-details disclaimers. difficulty (beginner/intermediate/advanced); source exactly "abstract"; model exactly "gpt-6-astra"; backgroundConcepts using only allowedBackgroundConcepts or []; and sourceAbstractSha256 exactly as supplied. The site shows the official abstract separately, unchanged. Do not repeat it verbatim, do not use generic filler, and do not add headings or markdown.

Evidence JSON:
{json.dumps(evidence, ensure_ascii=False)}
""" + (f"\nThe prior response failed validation. Correct only these issues:\n{json.dumps(retry_errors, ensure_ascii=False)}\n" if retry_errors else "")


def word_count(paragraphs: list[str]) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", " ".join(paragraphs)))


def validate_batch(records: list[Record], value: Any) -> tuple[dict[str, dict], dict[str, list[str]]]:
    if not isinstance(value, dict):
        return {}, {"_batch": ["Codex did not return a JSON object"]}
    expected = {record.slug: record for record in records}
    errors: dict[str, list[str]] = {}
    valid: dict[str, dict] = {}
    missing = sorted(set(expected) - set(value))
    unexpected = sorted(set(value) - set(expected))
    if missing:
        errors["_batch"] = [f"missing output slugs: {', '.join(missing[:5])}"]
    if unexpected:
        errors.setdefault("_batch", []).append(f"unexpected output slugs: {', '.join(unexpected[:5])}")
    validator = Draft202012Validator(SUMMARY_SCHEMA)
    for slug, record in expected.items():
        summary = value.get(slug)
        messages = [error.message for error in validator.iter_errors(summary)]
        if isinstance(summary, dict):
            if summary.get("sourceAbstractSha256") != record.source_hash:
                messages.append("sourceAbstractSha256 does not match supplied official abstract")
            if summary.get("source") != "abstract":
                messages.append("source must be 'abstract'")
            if summary.get("model") != MODEL:
                messages.append(f"model must be {MODEL!r}")
            paragraphs = summary.get("paragraphs", [])
            if isinstance(paragraphs, list):
                words = word_count([value for value in paragraphs if isinstance(value, str)])
                if not WORD_MIN <= words <= WORD_MAX:
                    messages.append(f"paragraphs have {words} words; expected {WORD_MIN}–{WORD_MAX}")
                invalid_backgrounds = set(summary.get("backgroundConcepts", [])) - set(record.topic_slugs)
                if invalid_backgrounds:
                    messages.append("backgroundConcepts includes a slug not supplied for this paper")
        if messages:
            errors[slug] = messages
        elif isinstance(summary, dict):
            valid[slug] = summary
    return valid, errors


def existing_valid(path: Path, records: list[Record]) -> bool:
    if not path.exists():
        return False
    try:
        _, errors = validate_batch(records, read_json(path))
        return not errors
    except Exception:
        return False


def valid_subset(records: list[Record], value: Any) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Validate a partial checkpoint one supplied slug at a time."""
    if not isinstance(value, dict):
        return {}, {"_batch": ["partial checkpoint is not a JSON object"]}
    expected = {record.slug: record for record in records}
    valid: dict[str, dict] = {}
    errors: dict[str, list[str]] = {}
    for slug, summary in value.items():
        record = expected.get(slug)
        if not record:
            errors[slug] = ["partial checkpoint slug is not in this batch"]
            continue
        accepted, messages = validate_batch([record], {slug: summary})
        if messages:
            errors[slug] = sum(messages.values(), [])
        else:
            valid.update(accepted)
    return valid, errors


def parse_reported_usage(stdout: str) -> dict[str, int] | None:
    """Read only token usage reported by Codex JSONL; missing values stay unknown."""
    totals: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = key.casefold().replace("-", "_")
                if normalized in {"input_tokens", "output_tokens", "total_tokens"} and isinstance(item, int):
                    totals[normalized] = max(totals.get(normalized, 0), item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for line in stdout.splitlines():
        try:
            visit(json.loads(line))
        except json.JSONDecodeError:
            continue
    return totals or None


def run_batch(root: Path, batch_number: int, records: list[Record], output_dir: Path, working_dir: Path, dry_run: bool, rate_gate: RateLimitGate | None = None) -> dict[str, Any]:
    started_at = _now()
    started_monotonic = time.monotonic()

    def result(status: str, path: Path, **extra: Any) -> dict[str, Any]:
        return {
            "batch": batch_number,
            "status": status,
            "path": str(path),
            "records": len(records),
            "startedAt": started_at,
            "finishedAt": _now(),
            "durationSeconds": round(time.monotonic() - started_monotonic, 3),
            **extra,
        }

    output_path = output_dir / f"batch-{batch_number:05d}.json"
    attempt_path = working_dir / f"batch-{batch_number:05d}.attempts.json"
    prior_attempts = read_json(attempt_path, [])
    if not isinstance(prior_attempts, list):
        prior_attempts = []
    if existing_valid(output_path, records):
        return result("skipped", output_path, attemptsDetail=prior_attempts)
    if dry_run:
        return result("planned", output_path)
    schema_path = working_dir / f"schema-batch-{batch_number:05d}.json"
    temporary = working_dir / f"response-batch-{batch_number:05d}.json"
    partial_path = working_dir / f"batch-{batch_number:05d}.partial.json"
    accepted, _ = valid_subset(records, read_json(partial_path, {}))
    attempts: list[dict[str, Any]] = list(prior_attempts)
    retry_errors: dict[str, list[str]] | None = None
    # A restart is a new bounded retry epoch. Keep every prior attempt in the
    # journal for accounting, but do not permanently strand a batch merely
    # because an earlier network/model outage exhausted its three attempts.
    first_attempt = len(attempts) + 1
    for attempt in range(first_attempt, first_attempt + MAX_ATTEMPTS):
        remaining = [record for record in records if record.slug not in accepted]
        if not remaining:
            write_json(output_path, accepted)
            partial_path.unlink(missing_ok=True)
            return result("complete", output_path, attempts=attempt - 1, attemptsDetail=attempts)
        write_json(schema_path, batch_schema(remaining))
        prompt = prompt_for(remaining, retry_errors)
        command = [
            "codex", *disabled_configured_mcp_overrides(), "exec", "--ephemeral", "--json", "-m", MODEL,
            "--sandbox", "read-only", "--output-schema", str(schema_path),
            "-o", str(temporary), "-C", str(root), "-",
        ]
        try:
            if rate_gate is not None:
                rate_gate.wait()
            process = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=900, check=False)
        except subprocess.TimeoutExpired:
            attempts.append({"attempt": attempt, "error": "timed out after 900 seconds"})
            write_json(attempt_path, attempts)
        else:
            usage = parse_reported_usage(process.stdout)
            if process.returncode == 0 and temporary.exists():
                try:
                    valid, errors = validate_batch(remaining, read_json(temporary))
                except Exception as error:
                    valid, errors = {}, {"_batch": [f"invalid JSON response: {type(error).__name__}: {error}"]}
                accepted.update(valid)
                if valid:
                    write_json(partial_path, accepted)
                if len(accepted) == len(records):
                    attempts.append({"attempt": attempt, "usage": usage, "status": "accepted"})
                    write_json(attempt_path, attempts)
                    write_json(output_path, accepted)
                    temporary.unlink(missing_ok=True)
                    partial_path.unlink(missing_ok=True)
                    return result("complete", output_path, attempts=attempt, attemptsDetail=attempts)
                attempts.append({"attempt": attempt, "errors": errors, "usage": usage})
                retry_errors = errors
            else:
                # `codex exec --json` reports operational failures on stdout as
                # JSONL, so retain bounded diagnostic tails from both streams.
                # They are checkpoint metadata only; no prompts or abstracts are
                # written here.
                attempts.append({
                    "attempt": attempt,
                    "returncode": process.returncode,
                    "usage": usage,
                    "stderr": process.stderr[-2000:],
                    "stdout": process.stdout[-2000:],
                })
            write_json(attempt_path, attempts)
        temporary.unlink(missing_ok=True)
        if attempt < first_attempt + MAX_ATTEMPTS - 1:
            diagnostic = " ".join(
                str(item.get("stderr", "")) + " " + str(item.get("stdout", ""))
                for item in attempts[-1:]
                if isinstance(item, dict)
            ).casefold()
            # Provider rate limits need a slower bounded backoff than a schema
            # retry. This remains explicit instead of guessing a reset window.
            limited = any(
                marker in diagnostic for marker in (
                    "rate limit", "rate_limit", "429", "quota", "usage limit", "too many requests",
                )
            )
            epoch_attempt = attempt - first_attempt
            delay = min(60, 5 * 2 ** epoch_attempt) if limited else 2 ** epoch_attempt
            if limited and rate_gate is not None:
                rate_gate.defer(delay)
                rate_gate.wait()
            else:
                time.sleep(delay)
    error_path = working_dir / f"batch-{batch_number:05d}.errors.json"
    write_json(error_path, {"records": [record.slug for record in records], "attempts": attempts})
    return result("failed", error_path, attempts=len(attempts), attemptsDetail=attempts)


def make_manifest(records: list[Record], unavailable: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
    evidence_input = sum((len(record.title) + len(record.abstract)) // 4 for record in records)
    estimated_calls = (len(records) + args.batch_size - 1) // args.batch_size
    unavailable_counts: dict[str, int] = {}
    for item in unavailable:
        unavailable_counts[item["reason"]] = unavailable_counts.get(item["reason"], 0) + 1
    return {
        "model": MODEL,
        "input": str(args.input),
        "inputHash": digest,
        "validationVersion": VALIDATION_VERSION,
        "eligibleRecords": len(records),
        "eligibleSnapshot": [{"slug": record.slug, "sourceAbstractSha256": record.source_hash} for record in records],
        "unavailableCount": len(unavailable),
        "unavailableByReason": unavailable_counts,
        "batchSize": args.batch_size,
        "workers": args.workers,
        "estimatedEvidenceTokens": evidence_input,
        "estimatedContextOverheadTokens": estimated_calls * PROBE_CONTEXT_OVERHEAD_TOKENS,
        "estimatedInputTokens": evidence_input + estimated_calls * PROBE_CONTEXT_OVERHEAD_TOKENS,
        "estimatedOutputTokens": len(records) * 450,
        "estimateBasis": "one-record Astra probe: 17,955 reported input tokens for 208 evidence tokens; actual usage is recorded separately",
        "dollarCost": "unknown; local Codex account usage is not an API price quote",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSON list from offline metadata or summary prompts")
    parser.add_argument("--output-dir", required=True, type=Path, help="directory for validated slug→summary shards")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, help="author only the first N eligible records")
    parser.add_argument("--exclude-file", type=Path, help="JSON or line-delimited paper slugs to leave untouched")
    parser.add_argument("--skip-existing-dir", type=Path, help="skip only current-hash-valid summaries in importable top-level shards")
    parser.add_argument("--dry-run", action="store_true", help="write a manifest and planned batches without calling Codex")
    parser.add_argument("--max-failures", type=int, default=8, help="stop scheduling after this many failed batches")
    parser.add_argument("--drain-file", type=Path, help="file whose presence drains new scheduling after in-flight batches finish")
    return parser.parse_args(argv)


def _restore_frozen_records(records: list[Record], prior_manifest: dict[str, Any]) -> list[Record]:
    """Keep batch boundaries stable on resume, independent of later shard writes."""
    snapshot = prior_manifest.get("eligibleSnapshot")
    if not isinstance(snapshot, list):
        raise SystemExit("existing run lacks an eligible-record snapshot; choose a new --output-dir")
    current = {record.slug: record for record in records}
    restored: list[Record] = []
    for item in snapshot:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not isinstance(item.get("sourceAbstractSha256"), str):
            raise SystemExit("existing run has an invalid eligible-record snapshot; choose a new --output-dir")
        record = current.get(item["slug"])
        if record is None or record.source_hash != item["sourceAbstractSha256"]:
            raise SystemExit("input no longer matches the frozen eligible-record snapshot; choose a new --output-dir")
        restored.append(record)
    return restored


def _usage_ledger(working_dir: Path) -> list[dict[str, Any]]:
    """Derive durable usage once from append-only, per-batch attempt journals."""
    ledger: list[dict[str, Any]] = []
    for path in sorted(working_dir.glob("batch-*.attempts.json")):
        match = re.search(r"batch-(\d+)\.attempts\.json$", path.name)
        attempts = read_json(path, [])
        if not match or not isinstance(attempts, list):
            continue
        for detail in attempts:
            if isinstance(detail, dict) and isinstance(detail.get("usage"), dict):
                ledger.append({"batch": int(match.group(1)), "attempt": detail.get("attempt"), "usage": detail["usage"]})
    return ledger


def _run_locked(args: argparse.Namespace, root: Path, working_dir: Path) -> int:
    """Run one owner of an output directory; callers hold its advisory lock."""
    manifest_path = working_dir / "manifest.json"
    prior_manifest = read_json(manifest_path, {})
    if args.skip_existing_dir:
        output = args.output_dir.resolve()
        skip = args.skip_existing_dir.resolve()
        if skip == output or output.is_relative_to(skip):
            raise SystemExit("--skip-existing-dir cannot be the active output directory or one of its parents; use a separate immutable summary directory")
    base_records, base_unavailable = load_records(args.input, None, args.exclude_file, None)
    if prior_manifest:
        current_identity = {
            "inputHash": hashlib.sha256(args.input.read_bytes()).hexdigest(),
            "batchSize": args.batch_size,
            "model": MODEL,
            "validationVersion": VALIDATION_VERSION,
        }
        if any(prior_manifest.get(key) not in {None, current_identity[key]} for key in current_identity):
            raise SystemExit("output directory belongs to a different input, batch size, model, or validation revision; choose a new --output-dir")
        records = _restore_frozen_records(base_records, prior_manifest)
        saved_unavailable = read_json(working_dir / "unavailable.json", base_unavailable)
        unavailable = saved_unavailable if isinstance(saved_unavailable, list) else base_unavailable
    else:
        records, unavailable = load_records(args.input, args.limit, args.exclude_file, args.skip_existing_dir)
    manifest = make_manifest(records, unavailable, args)
    previous_batches = {item.get("batch"): item for item in prior_manifest.get("batches", []) if isinstance(item, dict) and isinstance(item.get("batch"), int)}
    manifest["batches"] = [item for _, item in sorted(previous_batches.items())]
    manifest["reportedUsage"] = _usage_ledger(working_dir)
    write_json(manifest_path, manifest)
    write_json(working_dir / "unavailable.json", unavailable)
    control_path = _control_path(working_dir)
    if not control_path.exists():
        write_json(control_path, {"targetWorkers": args.workers, "updatedAt": _now()})
    drain_path = args.drain_file or working_dir / "DRAIN"
    drain_signal = threading.Event()

    def request_drain(_signum: int, _frame: Any) -> None:
        drain_signal.set()

    old_signals = {signal.SIGINT: signal.getsignal(signal.SIGINT), signal.SIGTERM: signal.getsignal(signal.SIGTERM)}
    signal.signal(signal.SIGINT, request_drain)
    signal.signal(signal.SIGTERM, request_drain)
    started = time.monotonic()
    batches = [records[index:index + args.batch_size] for index in range(0, len(records), args.batch_size)]
    results: dict[int, dict[str, Any]] = dict(previous_batches)
    baseline_complete = sum(
        int(item.get("records", 0))
        for item in results.values()
        if item.get("status") in {"complete", "skipped"}
    )
    failures = 0
    drained = False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            next_batch = 0
            futures: dict[concurrent.futures.Future[dict[str, Any]], tuple[int, float]] = {}
            rate_gate = RateLimitGate()

            def is_draining() -> bool:
                return drain_signal.is_set() or drain_path.exists()

            def active_rows() -> list[dict[str, Any]]:
                return [{"batch": batch, "records": len(batches[batch]), "startedAt": dt.datetime.fromtimestamp(started_at, dt.timezone.utc).isoformat()} for batch, started_at in sorted(futures.values())]

            def checkpoint() -> None:
                manifest["batches"] = [item for _, item in sorted(results.items())]
                manifest["reportedUsage"] = _usage_ledger(working_dir)
                write_json(manifest_path, manifest)
                _write_progress(working_dir, manifest, results, active_rows(), started, baseline_complete=baseline_complete, drain_requested=is_draining())

            def schedule() -> None:
                nonlocal next_batch, drained
                if is_draining():
                    drained = True
                    return
                target = _target_workers(working_dir, args.workers)
                while next_batch < len(batches) and len(futures) < target and failures < args.max_failures:
                    batch = batches[next_batch]
                    futures[pool.submit(run_batch, root, next_batch, batch, args.output_dir, working_dir, args.dry_run, rate_gate)] = (next_batch, time.time())
                    next_batch += 1

            schedule()
            checkpoint()
            while futures:
                done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    batch, _started_at = futures.pop(future)
                    result = future.result()
                    results[batch] = result
                    if result["status"] == "failed":
                        failures += 1
                schedule()
                checkpoint()
        final_results = [item for _, item in sorted(results.items())]
        manifest["batches"] = final_results
        manifest["reportedUsage"] = _usage_ledger(working_dir)
        manifest["completeRecords"] = sum(item["records"] for item in final_results if item["status"] in {"complete", "skipped"})
        manifest["failedBatches"] = sum(item["status"] == "failed" for item in final_results)
        write_json(manifest_path, manifest)
        _write_progress(working_dir, manifest, results, [], started, baseline_complete=baseline_complete, drain_requested=drained)
        print(json.dumps({"eligibleRecords": manifest["eligibleRecords"], "unavailableCount": manifest["unavailableCount"], "batchesFinished": len(final_results), "completeRecords": manifest["completeRecords"], "failedBatches": manifest["failedBatches"], "drained": drained, "reportedUsage": manifest["reportedUsage"]}, sort_keys=True))
        return 2 if drained else (1 if manifest["failedBatches"] else 0)
    finally:
        for signum, previous in old_signals.items():
            signal.signal(signum, previous)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if not 1 <= args.workers <= MAX_WORKERS:
        raise SystemExit(f"--workers must be between 1 and {MAX_WORKERS}")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.max_failures < 1:
        raise SystemExit("--max-failures must be positive")
    root = Path.cwd()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    working_dir = args.output_dir / "working"
    working_dir.mkdir(parents=True, exist_ok=True)
    with RunLock(working_dir / ".runner.lock"):
        return _run_locked(args, root, working_dir)


if __name__ == "__main__":
    raise SystemExit(main())
