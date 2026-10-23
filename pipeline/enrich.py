from __future__ import annotations

import time
from pathlib import Path

import httpx

from .settings import Settings
from .corpus import select, write_meta
from .util import read_json, write_json


def _match_openalex(client: httpx.Client, paper: dict) -> dict | None:
    # Filter title/year locally: external search remains imperfect and is recorded as provenance.
    response = client.get(
        "https://api.openalex.org/works",
        params={"search": paper["title"], "per-page": 5, "select": "id,title,publication_year,cited_by_count,referenced_works,ids"},
    )
    response.raise_for_status()
    candidates = response.json().get("results", [])
    title = paper["title"].casefold()
    for candidate in candidates:
        if candidate.get("title", "").casefold() == title and candidate.get("publication_year") == paper["year"]:
            return candidate
    return None


def enrich(settings: Settings, corpus: str = "sample") -> dict:
    papers, corpus_fingerprint = select(settings, corpus)
    if not settings.enrichment_enabled or settings.enrichment_provider == "none":
        output = settings.derived_dir / f"papers-enriched-{corpus}.json"
        write_json(output, papers)
        write_meta(output, corpus, corpus_fingerprint)
        return {"enabled": False, "matched": 0, "papers": len(papers)}
    if settings.enrichment_provider != "openalex":
        raise ValueError("Only OpenAlex is implemented; Semantic Scholar needs an API key/retry policy.")
    cache = settings.derived_dir / "openalex-matches.json"
    matches = read_json(cache, {})
    limit = settings.enrichment_max_requests or len(papers)
    requested = 0
    with httpx.Client(timeout=settings.timeout, headers={"User-Agent": "Latentscape research indexer/0.1"}) as client:
        for paper in papers:
            if paper["slug"] in matches or requested >= limit:
                continue
            try:
                matched = _match_openalex(client, paper)
                # Preserve a successful checkpoint; transient errors are logged but
                # intentionally not cached as completed records, so reruns retry.
                if matched:
                    matches[paper["slug"]] = matched
            except Exception as exc:
                write_json(settings.derived_dir / "openalex-errors.json", {paper["slug"]: f"{type(exc).__name__}: {exc}"})
            write_json(cache, matches)
            requested += 1
            time.sleep(max(settings.delay, 0.11))
    selected_slugs = {paper["slug"] for paper in papers}
    by_external = {value["id"]: slug for slug, value in matches.items() if value and value.get("id") and slug in selected_slugs}
    for paper in papers:
        matched = matches.get(paper["slug"])
        if not matched or not matched.get("id"):
            continue
        paper["citationCount"] = matched.get("cited_by_count")
        refs = [by_external[work] for work in matched.get("referenced_works", []) if work in by_external]
        paper["references"] = sorted(set(refs))
        paper["provenance"].append("OpenAlex")
    for paper in papers:
        paper["citedBy"] = []
    for paper in papers:
        for referenced in paper["references"]:
            target = next((p for p in papers if p["slug"] == referenced), None)
            if target:
                target["citedBy"].append(paper["slug"])
    output = settings.derived_dir / f"papers-enriched-{corpus}.json"
    write_json(output, papers)
    write_meta(output, corpus, corpus_fingerprint)
    return {"enabled": True, "matched": sum(1 for x in matches.values() if x and x.get("id")), "papers": len(papers), "output": str(output)}


def merge_cached_enrichment(settings: Settings, corpus: str = "sample") -> dict:
    """Apply successful OpenAlex checkpoints without issuing a network request."""
    papers, corpus_fingerprint = select(settings, corpus)
    matches = read_json(settings.derived_dir / "openalex-matches.json", {})
    selected = {paper["slug"] for paper in papers}
    external = {
        result["id"]: slug for slug, result in matches.items()
        if slug in selected and isinstance(result, dict) and result.get("id")
    }
    by_slug = {paper["slug"]: paper for paper in papers}
    matched = 0
    for paper in papers:
        result = matches.get(paper["slug"])
        if not isinstance(result, dict) or not result.get("id"):
            continue
        matched += 1
        paper["citationCount"] = result.get("cited_by_count")
        paper["references"] = sorted({external[ref] for ref in result.get("referenced_works", []) if ref in external})
        if "OpenAlex" not in paper["provenance"]:
            paper["provenance"].append("OpenAlex")
    for paper in papers:
        paper["citedBy"] = []
    for paper in papers:
        for reference in paper["references"]:
            by_slug[reference]["citedBy"].append(paper["slug"])
    output = settings.derived_dir / f"papers-enriched-{corpus}.json"
    write_json(output, papers)
    write_meta(output, corpus, corpus_fingerprint)
    return {"papers": len(papers), "matched": matched, "output": str(output)}
