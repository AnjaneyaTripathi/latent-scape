from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from .settings import Settings
from .ingest import _stratified_sample
from .corpus import select, verify_meta, write_meta
from .util import read_json, write_json

SUMMARY_SCHEMA = {
    "type": "object", "required": ["problem", "idea", "results", "limitations", "tldr", "difficulty", "source", "model", "backgroundConcepts"],
    "properties": {
        "problem": {"type": "string", "minLength": 1}, "idea": {"type": "string", "minLength": 1},
        "results": {"type": "string", "minLength": 1}, "limitations": {"type": "string", "minLength": 1},
        "tldr": {"type": "string", "minLength": 1}, "difficulty": {"enum": ["beginner", "intermediate", "advanced"]},
        "source": {"type": "string", "minLength": 1}, "model": {"type": "string", "minLength": 1},
        "backgroundConcepts": {"type": "array", "items": {"type": "string"}},
    }, "additionalProperties": False,
}

PROMPT_TEMPLATE = """Using only this title and abstract, return JSON matching the summary schema.
Do not invent experimental details. Explain uncertainty in limitations.
Title: {title}
Abstract: {abstract}
"""


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
    prompts = [{"paperSlug": p["slug"], "title": p["title"], "abstract": p.get("abstract", ""), "prompt": PROMPT_TEMPLATE.format(**p)} for p in batch]
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
            prompts[index] = {"paperSlug": new_slug, "title": replacement["title"], "abstract": replacement["abstract"], "prompt": PROMPT_TEMPLATE.format(**replacement)}
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
        updated = {"paperSlug": paper["slug"], "title": paper["title"], "abstract": paper["abstract"], "prompt": PROMPT_TEMPLATE.format(**paper)}
        if updated != prompt:
            prompts[index] = updated
            refreshed += 1
    write_json(prompts_path, prompts)
    return {"refreshed": refreshed, "batch": len(prompts)}


def import_summaries(settings: Settings, source: Path | None = None, corpus: str = "sample") -> dict:
    source = source or settings.derived_dir / f"summaries-import-{corpus}.json"
    if source.exists():
        imported = read_json(source, {})
    elif corpus == "sample":
        # Astra writes independent halves so authoring can run concurrently.
        imported = {}
        for path in sorted((settings.root / "data/generated").glob("summaries-astra-sample*.json")):
            imported.update(read_json(path, {}))
    else:
        imported = {}
    if not isinstance(imported, dict):
        raise ValueError("summary import must be a {paperSlug: summary} mapping")
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
    for slug, summary in imported.items():
        if slug not in by_slug:
            errors[slug] = ["paper slug absent from selected corpus"]
            continue
        messages = [error.message for error in validator.iter_errors(summary)]
        if messages:
            errors[slug] = messages
            continue
        clean = {key: summary[key] for key in ("problem", "idea", "results", "limitations", "tldr", "difficulty", "source", "model")}
        background = [topic for topic in summary["backgroundConcepts"] if topic in topics]
        clean["backgroundConcepts"] = background
        by_slug[slug]["summary"] = clean
        by_slug[slug]["topicSlugs"] = sorted(set(by_slug[slug]["topicSlugs"] + background))
    write_json(settings.derived_dir / "summary-import-errors.json", errors)
    write_json(papers_path, papers)
    write_meta(papers_path, corpus, value)
    return {"imported": sum(1 for p in papers if p.get("summary")), "errors": errors}
