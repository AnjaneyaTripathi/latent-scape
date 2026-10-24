from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .corpus import select, verify_meta, write_meta
from .ingest import _stratified_sample
from .settings import Settings
from .summary_evidence import summary_eligibility_reason
from .util import read_json, write_json

SUMMARY_SCHEMA = {
    "type": "object", "required": ["tldr", "difficulty", "source", "model", "backgroundConcepts"],
    "properties": {
        "problem": {"type": "string", "minLength": 1}, "idea": {"type": "string", "minLength": 1},
        "results": {"type": "string", "minLength": 1}, "limitations": {"type": "string", "minLength": 1},
        "tldr": {"type": "string", "minLength": 1}, "difficulty": {"enum": ["beginner", "intermediate", "advanced"]},
        "source": {"type": "string", "minLength": 1}, "model": {"type": "string", "minLength": 1},
        "backgroundConcepts": {"type": "array", "items": {"type": "string"}},
        "sourceAbstractSha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "paragraphs": {
            "type": "array", "minItems": 3, "maxItems": 5,
            "items": {"type": "string", "minLength": 80},
        },
    },
    "anyOf": [
        {"required": ["problem", "idea", "results", "limitations"]},
        {"required": ["paragraphs", "sourceAbstractSha256"]},
    ],
    "additionalProperties": False,
}

PROMPT_TEMPLATE = """Using only this title and official abstract, return JSON matching the narrative summary schema.
Write a TLDR and 3–5 substantive, plain-language paragraphs explaining what the paper did: the motivating problem, approach, evidence or stated findings, and limits of what the abstract establishes. Do not invent experimental details or rewrite the official abstract. Keep the supplied sourceAbstractSha256 exactly.
Title: {title}
Official abstract SHA-256: {abstract_hash}
Official abstract: {abstract}
"""


def abstract_sha256(paper: dict) -> str:
    """Bind a generated explanation to its exact official abstract text."""
    return hashlib.sha256(paper.get("abstract", "").encode("utf-8")).hexdigest()


def valid_astra_narrative(paper: dict) -> bool:
    """Return whether a stored narrative meets the release provenance contract."""
    summary = paper.get("summary")
    if not isinstance(summary, dict) or "paragraphs" not in summary:
        return False
    if summary.get("model") != "gpt-6-astra" or summary.get("source") != "abstract":
        return False
    if summary.get("sourceAbstractSha256") != abstract_sha256(paper):
        return False
    return not list(Draft202012Validator(SUMMARY_SCHEMA).iter_errors(summary))


def narrative_prompt(paper: dict) -> dict:
    return {
        "paperSlug": paper["slug"],
        "title": paper["title"],
        "abstract": paper.get("abstract", ""),
        "sourceAbstractSha256": abstract_sha256(paper),
        "prompt": PROMPT_TEMPLATE.format(
            title=paper["title"], abstract=paper.get("abstract", ""), abstract_hash=abstract_sha256(paper),
        ),
    }


def estimate_cost(papers: list[dict]) -> dict:
    # Human/Astra generation is explicitly zero external API spend; token count is informational.
    tokens = sum((len(p["title"]) + len(p.get("abstract", ""))) // 4 + 350 for p in papers)
    return {"papers": len(papers), "estimatedTokens": tokens, "externalApiUsd": 0.0, "provider": "human-or-astra"}


def write_summary_batch(settings: Settings, limit: int = 200, corpus: str = "sample") -> dict:
    source, value = select(settings, corpus)
    topics_papers = settings.derived_dir / f"papers-topics-{corpus}.json"
    if topics_papers.exists():
        verify_meta(topics_papers, corpus, value, "topic papers")
        papers = read_json(topics_papers, source)
    else:
        papers = source
    # The review gate must see both venues and the breadth of the selected sample.
    batch = _stratified_sample(papers, min(limit, len(papers)), settings.sample_seed)
    prompts = [narrative_prompt(paper) for paper in batch]
    suffix = "" if corpus == "sample" else f"-{corpus}"
    batch_path = settings.derived_dir / f"summary-batch-200{suffix}.json"
    cost_path = settings.derived_dir / f"summary-cost-estimate{suffix}.json"
    write_json(batch_path, prompts)
    write_json(cost_path, {"corpus": corpus, "fingerprint": value, **estimate_cost(batch)})
    return {"batch": len(batch), "path": str(batch_path), "cost": estimate_cost(batch)}


def replace_summary_prompt(settings: Settings, old_slug: str, new_slug: str) -> dict:
    """Substitute one repaired sample item while retaining batch ordering and other IDs."""
    prompts_path = settings.derived_dir / "summary-batch-200.json"
    prompts = read_json(prompts_path, [])
    papers = {p["slug"]: p for p in read_json(settings.raw_dir / "papers-sample.json", [])}
    replacement = papers.get(new_slug)
    if not replacement:
        raise ValueError("replacement paper is absent from the sample corpus")
    for index, prompt in enumerate(prompts):
        if prompt.get("paperSlug") == old_slug:
            prompts[index] = narrative_prompt(replacement)
            write_json(prompts_path, prompts)
            return {"replaced": True, "index": index, "newSlug": new_slug}
    return {"replaced": False, "reason": "old slug absent from summary batch"}


def refresh_summary_prompts(settings: Settings) -> dict:
    """Refresh metadata/abstracts in place while preserving the authoritative IDs/order."""
    prompts_path = settings.derived_dir / "summary-batch-200.json"
    prompts = read_json(prompts_path, [])
    papers = {p["slug"]: p for p in read_json(settings.raw_dir / "papers-sample.json", [])}
    refreshed = 0
    for index, prompt in enumerate(prompts):
        paper = papers.get(prompt.get("paperSlug"))
        if not paper:
            continue
        updated = narrative_prompt(paper)
        if updated != prompt:
            prompts[index] = updated
            refreshed += 1
    write_json(prompts_path, prompts)
    return {"refreshed": refreshed, "batch": len(prompts)}


def _ignored_summary_path(path: Path) -> bool:
    """Runner bookkeeping is never an importable editorial shard."""
    return bool({"working", "archive"} & set(path.parts))


def _summary_paths(settings: Settings, source: Path | None, corpus: str) -> tuple[list[tuple[Path, str]], bool, bool]:
    """Return (path, kind) candidates and whether a source is explicitly strict.

    A single explicitly supplied file is strict: a stale source hash must fail
    loudly. Directories are intentionally permissive because they are durable
    run folders containing superseded shards after a proceedings correction.
    """
    if source is not None:
        if source.is_dir():
            return [(path, "directory") for path in sorted(source.rglob("*.json")) if not _ignored_summary_path(path)], False, True
        return [(source, "file")], True, True
    staged = settings.derived_dir / f"summaries-import-{corpus}.json"
    if staged.exists():
        return [(staged, "staged")], False, False
    generated = settings.root / "data/generated"
    paths: list[tuple[Path, str]] = []
    if corpus == "sample":
        paths.extend((path, "legacy") for path in sorted(generated.glob("summaries-astra-sample*.json")))
    narratives = generated / "narratives"
    paths.extend((path, "narrative") for path in sorted(narratives.rglob("*.json")) if not _ignored_summary_path(path))
    return paths, False, False


def _is_summary_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(summary, dict) and "tldr" in summary for summary in value.values())


def import_summaries(settings: Settings, source: Path | None = None, corpus: str = "sample") -> dict:
    paths, strict_file, _explicit_directory = _summary_paths(settings, source, corpus)
    validator = Draft202012Validator(SUMMARY_SCHEMA)
    source_papers, value = select(settings, corpus)
    papers_path = settings.derived_dir / f"papers-topics-{corpus}.json"
    if papers_path.exists():
        verify_meta(papers_path, corpus, value, "topic papers")
        papers = read_json(papers_path, source_papers)
    else:
        papers = source_papers
    topics_path = settings.derived_dir / f"topics-{corpus}.json"
    topics = {x["slug"] for x in read_json(topics_path, [])}
    errors: dict[str, list[str]] = {}
    by_slug = {paper["slug"]: paper for paper in papers}
    candidates: dict[str, list[tuple[dict, Path, str]]] = {}
    skipped_stale = 0
    skipped_outside_corpus = 0
    skipped_ineligible: set[str] = set()
    ineligible: dict[str, str] = {}
    # A source audit can quarantine an abstract after a narrative was imported.
    # Clear that prose first so it cannot remain visible while the evidence is
    # awaiting repair.
    for slug, paper in by_slug.items():
        reason = summary_eligibility_reason(
            str(paper.get("abstract", "")), str(paper.get("abstractQuality", ""))
        )
        if reason is None:
            continue
        ineligible[slug] = reason
        summary = paper.get("summary")
        if isinstance(summary, dict) and "paragraphs" in summary:
            paper.pop("summary", None)
            skipped_ineligible.add(slug)
    for path, kind in paths:
        rows = read_json(path, {})
        if not _is_summary_map(rows):
            if strict_file:
                raise ValueError("summary import must be a {paperSlug: summary} mapping")
            continue
        for slug, summary in rows.items():
            if slug not in by_slug:
                if strict_file:
                    errors[slug] = ["paper slug absent from selected corpus"]
                else:
                    skipped_outside_corpus += 1
                continue
            if slug in ineligible:
                if strict_file:
                    errors[slug] = [f"official abstract is ineligible for summaries: {ineligible[slug]}"]
                else:
                    skipped_ineligible.add(slug)
                continue
            if not isinstance(summary, dict):  # guarded by _is_summary_map
                continue
            candidates.setdefault(slug, []).append((summary, path, kind))

    accepted_narrative = 0
    accepted_legacy = 0
    for slug, rows in candidates.items():
        paper = by_slug[slug]
        expected_hash = abstract_sha256(paper)
        valid_narratives: list[dict] = []
        valid_legacy: list[dict] = []
        row_errors: list[str] = []
        for summary, path, kind in rows:
            messages = [error.message for error in validator.iter_errors(summary)]
            narrative = "paragraphs" in summary
            if messages:
                if strict_file:
                    row_errors.extend(messages)
                continue
            supplied_hash = summary.get("sourceAbstractSha256")
            if narrative:
                if supplied_hash != expected_hash:
                    if strict_file:
                        row_errors.append("sourceAbstractSha256 does not match the official abstract in this corpus")
                    else:
                        skipped_stale += 1
                    continue
                if summary.get("model") != "gpt-6-astra" or summary.get("source") != "abstract":
                    if strict_file:
                        row_errors.append("narrative summaries must be Astra-authored from the official abstract")
                    continue
                valid_narratives.append(summary)
            else:
                # Legacy cards have no source hash. They are a temporary sample
                # fallback only, and lose to any current-hash narrative.
                if supplied_hash is not None and supplied_hash != expected_hash:
                    if strict_file:
                        row_errors.append("sourceAbstractSha256 does not match the official abstract in this corpus")
                    else:
                        skipped_stale += 1
                    continue
                valid_legacy.append(summary)
        if row_errors and strict_file:
            errors[slug] = sorted(set(row_errors))
            continue
        selected_pool = valid_narratives or valid_legacy
        if not selected_pool:
            continue
        unique = {json.dumps(summary, ensure_ascii=False, sort_keys=True): summary for summary in selected_pool}
        if len(unique) != 1:
            errors[slug] = ["conflicting current-hash summary candidates"]
            continue
        summary = next(iter(unique.values()))
        clean = {key: summary[key] for key in ("problem", "idea", "results", "limitations", "tldr", "difficulty", "source", "model", "paragraphs", "sourceAbstractSha256") if key in summary}
        background = [topic for topic in summary["backgroundConcepts"] if topic in topics]
        clean["backgroundConcepts"] = background
        paper["summary"] = clean
        paper["topicSlugs"] = sorted(set(paper["topicSlugs"] + background))
        if "paragraphs" in clean:
            accepted_narrative += 1
        else:
            accepted_legacy += 1
    write_json(settings.derived_dir / "summary-import-errors.json", errors)
    write_json(papers_path, papers)
    write_meta(papers_path, corpus, value)
    return {
        "imported": sum(1 for p in papers if p.get("summary")),
        "acceptedNarratives": accepted_narrative,
        "acceptedLegacy": accepted_legacy,
        "skippedOutsideCorpus": skipped_outside_corpus,
        "skippedStale": skipped_stale,
        "skippedIneligible": len(skipped_ineligible),
        "errors": errors,
    }
