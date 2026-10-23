"""Resumable, bounded-concurrency hydration for the full proceedings inventory.

The index crawl intentionally runs separately from abstract hydration.  This keeps
the expensive part restartable: each completed official landing page is cached by
stable paper slug and the assembled corpus is checkpointed after a bounded batch.
No PDF is fetched or stored.
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import tarfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup

from .ingest import extract_outbound_links
from .settings import Settings
from .util import read_json, write_json


USER_AGENT = "Latentscape full proceedings indexer/0.1 (metadata only)"
PMLR_ARCHIVE = "https://codeload.github.com/mlresearch/v{volume}/tar.gz/refs/heads/gh-pages"
NEURIPS_VIRTUAL = "https://neurips.cc/static/virtual/data/neurips-{year}-orals-posters.json"
# These modern PMLR volumes have been verified by tree audit to be metadata-only.
# Keep this deliberately narrow: older volume repositories can contain PDFs and
# must use their normal official landing pages instead.
MODERN_PMLR_VOLUMES = {"202", "235", "267", "306"}
MIN_SUMMARY_EVIDENCE_WORDS = 40
MAX_JUNK_ABSTRACT_WORDS = 3
MAX_ABSTRACT_CHARACTERS = 3500
ABSTRACT_PLACEHOLDERS = {"abstract unavailable", "not available", "n/a", "none"}
LEGACY_FULL_BODY_MARKERS = re.compile(r"\b(?:acknowledg(?:e)?ments?|references)\b", re.IGNORECASE)


def _normalise_source_text(value: str | None) -> str:
    """Keep source wording intact while making HTML line breaks searchable.

    Source pages encode paragraphs and indentation differently across years.  A
    single whitespace normalisation is the only transformation performed here.
    """
    return " ".join((value or "").split())


def hydrate_official_page(page_html: str, paper: dict[str, Any]) -> dict[str, Any]:
    """Return a paper with its source abstract and explicit outbound links.

    An abstract heading is preferred over a generic meta description.  Both the
    display ``abstract`` and ``originalAbstract`` carry the same source text so
    downstream summaries never replace the official abstract.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    # PMLR's TeX expressions contain literal ``<``. BeautifulSoup then treats a
    # fragment such as ``$P_T< ... <P_S$`` as an unclosed HTML tag, swallowing
    # the bibliography controls into ``#abstract``. Bound the source fragment
    # before parsing so malformed TeX cannot consume following page chrome.
    raw_abstract = re.search(
        r'<div[^>]+(?:id=["\']abstract["\']|class=["\'][^"\']*\babstract\b[^"\']*["\'])[^>]*>(.*?)</div>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if raw_abstract:
        # Remove only ordinary presentation tags. A general HTML parser is not
        # safe here because TeX inequality notation also contains ``<``.
        source = re.sub(
            r"</?(?:em|i|b|strong|sup|sub|span|a)(?:\s+[^>]*)?>",
            " ",
            raw_abstract.group(1),
            flags=re.IGNORECASE,
        )
        abstract = _normalise_source_text(html.unescape(source))
    else:
        abstract = ""
    if not abstract:
        abstract_node = soup.select_one("#abstract, .abstract")
        abstract = _normalise_source_text(abstract_node.get_text(" ")) if abstract_node else ""
    if not abstract:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if _normalise_source_text(heading.get_text(" ")).casefold() != "abstract":
                continue
            candidate = heading.find_next_sibling(["p", "div"])
            if candidate:
                abstract = _normalise_source_text(candidate.get_text(" "))
                break
    if not abstract:
        meta = soup.select_one('meta[name="citation_abstract"], meta[name="description"]')
        if meta and meta.get("content"):
            abstract = _normalise_source_text(meta["content"])

    paper = dict(paper)
    paper["abstract"] = abstract
    paper["originalAbstract"] = abstract
    paper["abstractStatus"] = "present" if abstract else "missing"
    paper["abstractSource"] = "official-landing-page" if abstract else "official-landing-page-missing"
    # A handful of legacy proceedings pages place body text in the same DOM node
    # as the abstract. Preserve it rather than guessing a truncation boundary,
    # but make the condition visible to downstream curation.
    paper["abstractQuality"] = (
        "official-landing-page-possibly-full-text" if len(abstract) > 3000 else "official-landing-page"
    ) if abstract else "missing"
    code, project = extract_outbound_links(abstract, soup)
    paper["codeUrl"] = code or paper.get("codeUrl")
    paper["projectUrl"] = project or paper.get("projectUrl")
    return paper


def _cache_path(settings: Settings, paper: dict[str, Any]) -> Path:
    return settings.cache_dir / "paper-pages" / f"{paper['slug']}.html"


def _pmlr_volume(url: str) -> str | None:
    match = re.search(r"/v(\d+)(?:/|$)", url)
    return match.group(1) if match else None


def _pmlr_posts(archive: Path, year: int) -> dict[str, str]:
    """Map normalized title keys to official PMLR abstract front matter."""
    values: dict[str, str] = {}
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle:
            if not member.isfile() or "/_posts/" not in member.name or not member.name.endswith(".md"):
                continue
            handle = bundle.extractfile(member)
            if handle is None:
                continue
            try:
                text = handle.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not text.startswith("---\n"):
                continue
            closing = text.find("\n---", 4)
            if closing < 0:
                continue
            try:
                frontmatter = yaml.safe_load(text[4:closing]) or {}
            except yaml.YAMLError:
                continue
            title = str(frontmatter.get("title") or "")
            abstract = str(frontmatter.get("abstract") or "")
            if title and abstract:
                # A volume may include related material; exact year/title matching
                # below prevents it from entering this conference inventory.
                from .util import paper_key

                values[paper_key(title, year)] = _normalise_source_text(abstract)
    return values


async def _pmlr_archive_is_metadata_only(
    client: httpx.AsyncClient, settings: Settings, volume: str
) -> tuple[bool, str | None]:
    """Audit a repository tree before downloading any archive.

    Old PMLR volume repositories contain published PDFs.  Their landing pages
    remain a valid source, but downloading those repository archives would
    violate Latentscape's no-PDF rule.  The tree itself contains only paths and
    sizes, not paper contents.
    """
    cache = settings.cache_dir / "pmlr" / f"v{volume}-tree.json"
    if cache.exists():
        try:
            tree = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tree = None
    else:
        tree = None
    if tree is None:
        url = f"https://api.github.com/repos/mlresearch/v{volume}/git/trees/gh-pages?recursive=1"
        try:
            response = await client.get(url)
            response.raise_for_status()
            tree = response.json()
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
            temporary.replace(cache)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
    entries = tree.get("tree", []) if isinstance(tree, dict) else []
    if not entries:
        return False, "repository tree missing entries"
    return not any(str(entry.get("path", "")).casefold().endswith(".pdf") for entry in entries), None


async def _download_pmlr_archive(
    client: httpx.AsyncClient, settings: Settings, volume: str, retries: int
) -> tuple[Path | None, str | None]:
    path = settings.cache_dir / "pmlr" / f"v{volume}-gh-pages.tar.gz"
    if path.exists() and path.stat().st_size:
        return path, None
    url = PMLR_ARCHIVE.format(volume=volume)
    for attempt in range(retries + 1):
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tar.gz.tmp")
                with temporary.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
                temporary.replace(path)
            return path, None
        except (httpx.HTTPError, OSError) as error:
            if attempt >= retries:
                return None, f"{type(error).__name__}: {error}"
            await asyncio.sleep(min(45.0, 2.0 * (2**attempt)) + random.random() * 0.5)
    raise AssertionError("unreachable")


async def hydrate_pmlr_bulk(
    client: httpx.AsyncClient,
    settings: Settings,
    papers: list[dict[str, Any]],
    errors: dict[str, str],
    retries: int,
) -> int:
    """Hydrate ICML papers from PMLR's official, metadata-only source archives."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for paper in papers:
        if paper.get("venue") != "ICML" or paper.get("abstractStatus") != "pending":
            continue
        volume = _pmlr_volume(paper.get("url", ""))
        if volume:
            grouped.setdefault(volume, []).append(paper)

    hydrated = 0
    for volume, records in sorted(grouped.items(), key=lambda item: int(item[0])):
        if volume not in MODERN_PMLR_VOLUMES:
            continue
        metadata_only, audit_error = await _pmlr_archive_is_metadata_only(client, settings, volume)
        if not metadata_only:
            # A false result is intentionally a fallback, not an error: individual
            # official landing pages contain the same metadata without PDFs.
            if audit_error:
                errors[f"pmlr-volume-{volume}-tree"] = audit_error
            continue
        archive, error = await _download_pmlr_archive(client, settings, volume, retries)
        if error or archive is None:
            # Keep rows pending for the official landing-page fallback; record the
            # archive issue as information rather than treating it as missing data.
            continue
        try:
            posts = _pmlr_posts(archive, int(records[0]["year"]))
        except (OSError, tarfile.TarError) as exc:
            errors[f"pmlr-volume-{volume}"] = f"{type(exc).__name__}: {exc}"
            continue
        for paper in records:
            from .util import paper_key

            abstract = posts.get(paper_key(paper["title"], paper["year"]))
            if not abstract:
                continue
            paper["abstract"] = abstract
            paper["originalAbstract"] = abstract
            paper["abstractStatus"] = "present"
            paper["abstractSource"] = "official-pmlr-frontmatter"
            paper["abstractQuality"] = "official-pmlr-frontmatter"
            hydrated += 1
    return hydrated


async def hydrate_neurips_virtual_bulk(
    client: httpx.AsyncClient,
    papers: list[dict[str, Any]],
    errors: dict[str, str],
) -> int:
    """Use the official NeurIPS virtual-program JSON where it is published.

    The JSON is available for 2020--2025 and includes accepted-paper abstracts.
    We only accept records whose normalized title/year already exists in the
    proceedings inventory; virtual-only events cannot leak into the corpus.
    """
    from .util import paper_key

    grouped: dict[int, list[dict[str, Any]]] = {}
    for paper in papers:
        if paper.get("venue") == "NeurIPS" and paper.get("abstractStatus") == "pending" and 2020 <= paper["year"] <= 2025:
            grouped.setdefault(paper["year"], []).append(paper)
    hydrated = 0
    for year, records in sorted(grouped.items()):
        url = NEURIPS_VIRTUAL.format(year=year)
        try:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            source_records = payload.get("results", []) if isinstance(payload, dict) else []
        except (httpx.HTTPError, ValueError) as exc:
            errors[f"neurips-virtual-{year}"] = f"{type(exc).__name__}: {exc}"
            continue
        abstracts = {
            paper_key(str(row.get("name", "")), year): _normalise_source_text(str(row.get("abstract", "")))
            for row in source_records
            if row.get("name") and row.get("abstract")
        }
        for paper in records:
            abstract = abstracts.get(paper_key(paper["title"], year))
            if not abstract:
                continue
            paper["abstract"] = abstract
            paper["originalAbstract"] = abstract
            paper["abstractStatus"] = "present"
            paper["abstractSource"] = "official-neurips-virtual-program"
            paper["abstractQuality"] = "official-neurips-virtual-program"
            hydrated += 1
    return hydrated


def _load_cached(settings: Settings, paper: dict[str, Any]) -> dict[str, Any] | None:
    path = _cache_path(settings, paper)
    if not path.exists():
        return None
    try:
        return hydrate_official_page(path.read_text(encoding="utf-8"), paper)
    except (OSError, UnicodeDecodeError):
        return None


async def _fetch_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    settings: Settings,
    paper: dict[str, Any],
    retries: int,
) -> tuple[dict[str, Any], str | None]:
    cached = _load_cached(settings, paper)
    if cached is not None:
        return cached, None

    path = _cache_path(settings, paper)
    for attempt in range(retries + 1):
        try:
            async with semaphore:
                async with client.stream("GET", paper["url"]) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"retryable status {response.status_code}", request=response.request, response=response
                        )
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").casefold()
                    if "pdf" in content_type:
                        return (
                            dict(
                                paper,
                                abstract="",
                                originalAbstract="",
                                abstractStatus="missing",
                                abstractSource="official-pdf-not-downloaded",
                                abstractQuality="needs-pdf-abstract-extraction",
                            ),
                            None,
                        )
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        if not chunks and chunk.startswith(b"%PDF-"):
                            return (
                                dict(
                                    paper,
                                    abstract="",
                                    originalAbstract="",
                                    abstractStatus="missing",
                                    abstractSource="official-pdf-not-downloaded",
                                    abstractQuality="needs-pdf-abstract-extraction",
                                ),
                                None,
                            )
                        chunks.append(chunk)
            body = b"".join(chunks)
            text = body.decode(response.encoding or "utf-8", errors="replace")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".html.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
            return hydrate_official_page(text, paper), None
        except (httpx.HTTPError, OSError, UnicodeError) as error:
            if attempt >= retries:
                return (
                    dict(
                        paper,
                        abstract="",
                        originalAbstract="",
                        abstractStatus="retryable-error",
                        abstractSource="fetch-failed",
                        abstractQuality="retryable-error",
                    ),
                    f"{type(error).__name__}: {error}",
                )
            # Small jitter prevents a synchronized retry wave after a venue limit.
            retry_after = None
            if isinstance(error, httpx.HTTPStatusError):
                retry_after = error.response.headers.get("retry-after")
            try:
                wait = float(retry_after) if retry_after else min(30.0, 0.8 * (2**attempt))
            except ValueError:
                wait = min(30.0, 0.8 * (2**attempt))
            await asyncio.sleep(wait + random.random() * 0.35)
    raise AssertionError("unreachable")


def _progress(settings: Settings, papers: list[dict[str, Any]], errors: dict[str, str], started: float) -> dict[str, Any]:
    statuses = Counter(paper.get("abstractStatus", "pending") for paper in papers)
    return {
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "total": len(papers),
        "byStatus": dict(sorted(statuses.items())),
        "errors": len(errors),
        "byVenue": dict(Counter(paper["venue"] for paper in papers)),
    }


async def hydrate_full_async(
    settings: Settings,
    *,
    workers: int = 6,
    checkpoint_every: int = 100,
    retries: int = 3,
    limit: int | None = None,
    use_pmlr_bulk: bool = True,
    use_neurips_bulk: bool = True,
) -> dict[str, Any]:
    """Hydrate the current full metadata inventory, checkpointing after each batch."""
    if workers < 1 or checkpoint_every < 1:
        raise ValueError("workers and checkpoint_every must be positive")
    settings.prepare()
    full_path = settings.raw_dir / "papers-full.json"
    inventory_path = settings.raw_dir / "papers-inventory.json"
    papers = read_json(full_path, None)
    if papers is None:
        papers = read_json(inventory_path, None)
    if not isinstance(papers, list) or not papers:
        raise FileNotFoundError("No full metadata inventory; run ingest-full metadata discovery first.")

    for paper in papers:
        if paper.get("originalAbstract") and not paper.get("abstract"):
            paper["abstract"] = paper["originalAbstract"]
        if paper.get("abstract"):
            paper.setdefault("originalAbstract", paper["abstract"])
            # Some historical metadata adapters initialize a record as missing
            # before a later official source fills its abstract. Source text wins.
            paper["abstractStatus"] = "present"
            paper.setdefault(
                "abstractQuality",
                "official-landing-page-possibly-full-text" if len(paper["abstract"]) > 3000 else "official-landing-page",
            )
        else:
            paper.setdefault("abstractStatus", "pending")

    # Write all source metadata before issuing any landing-page request.  Downstream
    # curation can inspect stable IDs immediately, even while hydration continues.
    write_json(full_path, papers)

    errors_path = settings.raw_dir / "full-hydration-errors.json"
    errors: dict[str, str] = read_json(errors_path, {})
    started = time.monotonic()
    semaphore = asyncio.Semaphore(workers)
    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    by_slug = {paper["slug"]: index for index, paper in enumerate(papers)}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.timeout), follow_redirects=True,
        headers={"User-Agent": USER_AGENT}, limits=limits,
    ) as client:
        if use_neurips_bulk:
            await hydrate_neurips_virtual_bulk(client, papers, errors)
            write_json(full_path, papers)
            write_json(errors_path, errors)
            write_json(settings.raw_dir / "full-hydration-progress.json", _progress(settings, papers, errors, started))
        # PMLR's own metadata repository has abstract front matter for its ICML
        # volumes. This replaces tens of thousands of otherwise equivalent
        # landing-page requests without touching PDFs.
        if use_pmlr_bulk:
            await hydrate_pmlr_bulk(client, settings, papers, errors, retries)
            write_json(full_path, papers)
            write_json(errors_path, errors)
            write_json(settings.raw_dir / "full-hydration-progress.json", _progress(settings, papers, errors, started))
        pending = [paper for paper in papers if paper.get("abstractStatus") in {"pending", "retryable-error"}]
        if limit is not None:
            pending = pending[:limit]
        for begin in range(0, len(pending), checkpoint_every):
            batch = pending[begin : begin + checkpoint_every]
            outcome = await asyncio.gather(
                *[_fetch_one(client, semaphore, settings, paper, retries) for paper in batch]
            )
            for hydrated, error in outcome:
                papers[by_slug[hydrated["slug"]]] = hydrated
                if error:
                    errors[hydrated["slug"]] = error
                else:
                    errors.pop(hydrated["slug"], None)
            write_json(full_path, papers)
            write_json(errors_path, errors)
            write_json(settings.raw_dir / "full-hydration-progress.json", _progress(settings, papers, errors, started))

    report = _progress(settings, papers, errors, started)
    write_json(settings.raw_dir / "full-hydration-progress.json", report)
    return report


def hydrate_full(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    """Synchronous CLI entry point for full resumable hydration."""
    return asyncio.run(hydrate_full_async(settings, **kwargs))


def audit_full_abstract_quality(settings: Settings) -> dict[str, Any]:
    """Quarantine implausible abstracts before they reach embeddings or the site.

    Legacy proceedings templates sometimes expose a navigation fragment as an
    abstract, while others put the full article body in the abstract container.
    Obvious junk is unsafe to present or summarize; genuine short abstracts stay
    visible but are excluded from automated narrative generation.  The source
    page remains in the local page cache, and the stage emits a recovery queue.
    """
    full_path = settings.raw_dir / "papers-full.json"
    papers = read_json(full_path, [])
    if not isinstance(papers, list):
        raise FileNotFoundError("No full corpus to audit")
    rejected: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for paper in papers:
        abstract = str(paper.get("abstract") or "")
        words = len(abstract.split())
        length = len(abstract)
        reason = None
        normalized = _normalise_source_text(abstract).casefold().rstrip(".")
        if abstract and (words <= MAX_JUNK_ABSTRACT_WORDS or normalized in ABSTRACT_PLACEHOLDERS):
            reason = "placeholder-or-extraction-junk"
        elif (
            abstract
            and length > MAX_ABSTRACT_CHARACTERS
            and paper.get("abstractQuality") == "official-landing-page-possibly-full-text"
            and LEGACY_FULL_BODY_MARKERS.search(abstract)
        ):
            reason = "legacy-landing-page-possibly-full-paper"
        if reason is None:
            if abstract and words < MIN_SUMMARY_EVIDENCE_WORDS:
                paper["summaryEvidenceStatus"] = "insufficient"
            if (
                abstract
                and length > MAX_ABSTRACT_CHARACTERS
                and paper.get("abstractQuality") == "official-landing-page-possibly-full-text"
            ):
                review.append(
                    {
                        "slug": paper.get("slug"),
                        "title": paper.get("title"),
                        "venue": paper.get("venue"),
                        "year": paper.get("year"),
                        "reason": "long-legacy-landing-page-needs-review",
                        "wordCount": words,
                        "characterCount": length,
                        "source": paper.get("abstractSource"),
                        "url": paper.get("url"),
                    }
                )
            continue
        previous_source = paper.get("abstractSource")
        paper.update(
            {
                "abstract": "",
                "originalAbstract": "",
                "abstractStatus": "needs-source-recovery",
                "abstractSource": f"source-rejected-{reason}",
                "abstractQuality": "needs-source-recovery",
            }
        )
        paper.setdefault("provenance", []).append(previous_source)
        rejected.append(
            {
                "slug": paper.get("slug"),
                "title": paper.get("title"),
                "venue": paper.get("venue"),
                "year": paper.get("year"),
                "reason": reason,
                "wordCount": words,
                "characterCount": length,
                "previousSource": previous_source,
                "url": paper.get("url"),
            }
        )
    write_json(full_path, papers)
    report = {
        "minimumSummaryEvidenceWords": MIN_SUMMARY_EVIDENCE_WORDS,
        "maximumJunkWords": MAX_JUNK_ABSTRACT_WORDS,
        "maximumCharacters": MAX_ABSTRACT_CHARACTERS,
        "rejected": rejected,
        "review": review,
        "counts": dict(Counter(item["reason"] for item in rejected)),
    }
    write_json(settings.raw_dir / "full-abstract-quality-audit.json", report)
    insufficient = sum(
        paper.get("summaryEvidenceStatus") == "insufficient" for paper in papers
    )
    return {
        "papers": len(papers),
        "rejected": len(rejected),
        "review": len(review),
        "summaryEvidenceInsufficient": insufficient,
        **report["counts"],
    }


def repair_cached_page_chrome(settings: Settings) -> dict[str, int]:
    """Reparse cached official pages whose old extractor captured page controls."""
    full_path = settings.raw_dir / "papers-full.json"
    papers = read_json(full_path, [])
    if not isinstance(papers, list):
        raise FileNotFoundError("No full corpus to repair")
    chrome = ("copy to clipboard", "download related material", "bibtex")
    repaired = 0
    for index, paper in enumerate(papers):
        abstract = str(paper.get("abstract") or "").casefold()
        if not any(marker in abstract for marker in chrome):
            continue
        cached = _load_cached(settings, paper)
        if not cached or any(marker in str(cached.get("abstract") or "").casefold() for marker in chrome):
            continue
        papers[index] = cached
        repaired += 1
    write_json(full_path, papers)
    return {"repaired": repaired}


def extract_full_outbound_links(settings: Settings) -> dict[str, int]:
    """Derive only explicit code/project links from finalized source abstracts."""
    full_path = settings.raw_dir / "papers-full.json"
    papers = read_json(full_path, [])
    if not isinstance(papers, list):
        raise FileNotFoundError("No full corpus to enrich")
    added_code = added_project = 0
    for paper in papers:
        code, project = extract_outbound_links(str(paper.get("abstract") or ""))
        if code and not paper.get("codeUrl"):
            paper["codeUrl"] = code
            added_code += 1
        if project and not paper.get("projectUrl"):
            paper["projectUrl"] = project
            added_project += 1
    write_json(full_path, papers)
    return {
        "papers": len(papers),
        "addedCode": added_code,
        "addedProject": added_project,
        "codeLinks": sum(bool(paper.get("codeUrl")) for paper in papers),
        "projectLinks": sum(bool(paper.get("projectUrl")) for paper in papers),
    }
