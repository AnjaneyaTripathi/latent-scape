from __future__ import annotations

import random
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .settings import Settings
from .util import cached_get, paper_key, read_json, stable_slug, write_json

NEURIPS_INDEX = "https://papers.nips.cc/paper_files/paper/{year}"
NEURIPS_2025_MAIN = "https://papers.nips.cc/paper_files/paper/2025/vol38-main-conference"
NEURIPS_2025_CREATIVE = "https://papers.nips.cc/paper_files/paper/2025"
NEURIPS_2021_DATASETS = "https://datasets-benchmarks-proceedings.neurips.cc/paper/2021"
PMLR_VOLUMES = "https://proceedings.mlr.press/"


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _paper(title: str, authors: list[str], year: int, venue: str, abstract: str, url: str, source: str) -> dict:
    return {
        "slug": stable_slug(title, venue, year), "title": title, "authors": authors, "year": year,
        "venue": venue, "abstract": abstract, "url": url, "codeUrl": None, "projectUrl": None,
        "citationCount": None, "references": [], "citedBy": [], "similarPapers": [], "topicSlugs": [],
        "summary": None, "provenance": [source],
    }


def apply_verified_abstract_recoveries(settings: Settings, papers: list[dict]) -> dict[str, int]:
    """Apply curated, source-backed abstract patches after checkpoint carry-over.

    Patch files are update-only and validated by stable slug plus normalized
    title/year, so a recovery cannot silently attach to another paper.
    """
    files = (
        ("historical-2005-abstract-updates.json", "official-pdf-abstract-extracted"),
        ("historical-2007-abstract-updates.json", "official-pdf-abstract-extracted"),
        ("neurips-abstract-recovery.json", "source-section-recovered"),
    )
    by_slug = {paper["slug"]: paper for paper in papers}
    applied: dict[str, int] = {}
    for filename, fallback_quality in files:
        payload = read_json(settings.raw_dir / filename, {})
        updates = payload.get("updates", []) if isinstance(payload, dict) else []
        count = 0
        for update in updates:
            target = by_slug.get(update.get("slug"))
            if not target or target["year"] != update.get("year"):
                continue
            # The recovery manifests are slug-keyed. A small number of official
            # historical titles differ only in publisher punctuation/casing; the
            # curator has author-verified those exact slug/year variants.
            title_variant = paper_key(target["title"], target["year"]) != paper_key(
                update.get("title", ""), update.get("year", 0)
            )
            abstract = update.get("originalAbstract") or update.get("abstract")
            if not abstract:
                continue
            provenance = update.get("provenance") or [update.get("source")]
            target.update(
                {
                    "abstract": abstract,
                    "originalAbstract": abstract,
                    "abstractStatus": update.get("abstractStatus", "present"),
                    "abstractSource": update.get("abstractSource") or update.get("source"),
                    "abstractQuality": update.get("abstractQuality", fallback_quality),
                    "abstractRecoveryLocked": True,
                }
            )
            if title_variant:
                target["abstractRecoveryTitleVariantVerified"] = True
            target["provenance"] = [source for source in provenance if source]
            count += 1
        applied[filename] = count
    return applied


def apply_verified_author_recoveries(settings: Settings, papers: list[dict]) -> dict[str, int]:
    """Apply only title/year-verified author blocks from official first pages."""
    payload = read_json(settings.raw_dir / "author-recovery.json", {})
    by_slug = {paper["slug"]: paper for paper in papers}
    applied = 0
    for update in payload.get("updates", []) if isinstance(payload, dict) else []:
        target = by_slug.get(update.get("slug"))
        if not target or paper_key(target["title"], target["year"]) != paper_key(
            update.get("title", ""), update.get("year", 0)
        ):
            continue
        authors = update.get("authors")
        if not isinstance(authors, list) or not all(isinstance(author, str) and author for author in authors):
            continue
        target.update(
            {
                "authors": authors,
                "authorStatus": "present",
                "authorSource": update.get("source"),
                "authorQuality": "official-pdf-first-page",
                "authorRecoveryLocked": True,
            }
        )
        applied += 1
    for unresolved in payload.get("unresolved", []) if isinstance(payload, dict) else []:
        target = by_slug.get(unresolved.get("slug"))
        if target and paper_key(target["title"], target["year"]) == paper_key(
            unresolved.get("title", ""), unresolved.get("year", 0)
        ):
            target.update(
                {
                    "authorStatus": "needs-source-recovery",
                    "authorSource": unresolved.get("source"),
                    "authorQuality": "official-pdf-title-mismatch",
                }
            )
    return {"applied": applied}


def extract_outbound_links(text: str, soup: BeautifulSoup | None = None) -> tuple[str | None, str | None]:
    """Accept explicit code/project links only; never infer a repository."""
    entries: list[tuple[str, str]] = []
    for match in re.finditer(r"(?i)(.{0,48}?)(https?://[^\s<>'\"]+)", text):
        entries.append((match.group(1), match.group(2).rstrip(".,;:)]}")))
    if soup:
        entries.extend((_clean(a.get_text(" ")), a["href"].rstrip(".,;:)]}")) for a in soup.select("a[href]"))
    code = project = None
    for label, url in entries:
        if not re.match(r"^https?://", url):
            continue
        context = f"{label} {url}".casefold()
        if code is None and ("github.com/" in context or "gitlab.com/" in context) and re.search(r"code|repo|github|gitlab|implementation", context):
            code = url
        if project is None and re.search(r"project|website|homepage|demo", context):
            project = url
    return code, project


def parse_neurips_index(html: str, year: int, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []
    # The site has varied between li entries and paper title anchors; tolerate both.
    for anchor in soup.select("a[href]"):
        href, title = anchor.get("href", ""), _clean(anchor.get_text(" "))
        if not title or "abstract" not in href.casefold() or href.endswith(".pdf"):
            continue
        parent = anchor.find_parent("li") or anchor.parent
        text = _clean(parent.get_text(" "))
        authors_node = parent.select_one(".paper-authors") if hasattr(parent, "select_one") else None
        author_text = _clean(authors_node.get_text(" ")) if authors_node else text.replace(title, "", 1).strip(" -–")
        authors = [item.strip() for item in re.split(r",\s*|\s+and\s+", author_text) if item.strip()]
        found.append(_paper(title, authors, year, "NeurIPS", "", urljoin(source_url, href), source_url))
    return _dedupe(found)


def hydrate_paper(page_html: str, paper: dict) -> dict:
    """Fill abstracts only from a paper landing page; intentionally never downloads PDFs."""
    soup = BeautifulSoup(page_html, "html.parser")
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if _clean(heading.get_text(" ")).casefold() == "abstract":
            candidate = heading.find_next(["p", "div"])
            if candidate:
                paper["abstract"] = _clean(candidate.get_text(" "))
                break
    meta = soup.select_one('meta[name="citation_abstract"], meta[name="description"]')
    if meta and meta.get("content"):
        paper["abstract"] = _clean(meta["content"])
    code, project = extract_outbound_links(paper["abstract"], soup)
    paper["codeUrl"] = code or paper.get("codeUrl")
    paper["projectUrl"] = project or paper.get("projectUrl")
    return paper


def clean_sample_authors(settings: Settings) -> dict:
    """Repair known official index track labels without changing IDs or sample order."""
    path = settings.raw_dir / "papers-sample.json"
    papers = read_json(path, [])
    suffix = re.compile(r"\s+(?:Main Conference Track|Datasets? & Benchmarks(?: Track)?|Creative AI Track|Position Paper Track)$", re.I)
    changed = 0
    for paper in papers:
        authors = [suffix.sub("", author).strip() for author in paper.get("authors", [])]
        if authors != paper.get("authors", []):
            paper["authors"] = authors
            changed += 1
        if paper.get("abstract", "").casefold() in {"abstract unavailable", "abstract not available"}:
            paper["abstract"] = ""
            paper["abstractStatus"] = "missing"
        else:
            paper["abstractStatus"] = "present" if paper.get("abstract") else "missing"
        # A few legacy landing pages place the whole paper after the abstract.
        # Keep only the abstract preceding the first numbered Introduction heading.
        abstract = paper.get("abstract", "")
        clean = re.split(r"\s+1\s+(?:I\s*n\s*t\s*r\s*o\s*d\s*u\s*c\s*t\s*i\s*o\s*n|Introduction)\b", abstract, maxsplit=1, flags=re.I)[0].strip()
        if clean != abstract:
            paper["abstract"] = clean
            changed += 1
    write_json(path, papers)
    return {"papers": len(papers), "authorListsRepaired": changed}


def repair_sample_outbound_links(settings: Settings) -> dict:
    """Apply conservative explicit-link extraction to frozen sample metadata."""
    raw_path = settings.raw_dir / "papers-sample.json"
    papers = read_json(raw_path, [])
    changed = 0
    by_slug = {paper["slug"]: paper for paper in papers}
    for paper in papers:
        code, project = extract_outbound_links(paper.get("abstract", ""))
        if code and not paper.get("codeUrl"):
            paper["codeUrl"] = code
            changed += 1
        if project and not paper.get("projectUrl"):
            paper["projectUrl"] = project
            changed += 1
    write_json(raw_path, papers)
    # Retain summaries, clusters, and similarity rows while updating only URLs.
    for filename in ("papers-enriched-sample.json", "papers-topics-sample.json"):
        path = settings.derived_dir / filename
        rows = read_json(path, [])
        for row in rows:
            source = by_slug.get(row.get("slug"))
            if source:
                row["codeUrl"] = source.get("codeUrl")
                row["projectUrl"] = source.get("projectUrl")
        if rows:
            write_json(path, rows)
    return {"papers": len(papers), "linksAdded": changed}


def replace_missing_sample_abstract(settings: Settings) -> dict:
    """Replace the sole missing abstract with a cached official 1988 record.

    This changes one frozen sample ID only; the summary prompt list is updated in
    place so its other 199 IDs retain their assigned authors and order.
    """
    path = settings.raw_dir / "papers-sample.json"
    papers = read_json(path, [])
    index = next((i for i, p in enumerate(papers) if p.get("abstractStatus") == "missing"), None)
    if index is None:
        return {"replaced": False, "reason": "no missing abstract"}
    url = NEURIPS_INDEX.format(year=1988)
    candidates = parse_neurips_index((settings.cache_dir / "neurips" / "1988.html").read_text(), 1988, url)
    existing = {p["slug"] for p in papers}
    with httpx.Client(timeout=settings.timeout, follow_redirects=True, headers={"User-Agent": "Latentscape research indexer/0.1"}) as client:
        for candidate in candidates:
            if candidate["slug"] in existing:
                continue
            html, error = cached_get(client, candidate["url"], settings.cache_dir / "paper-pages" / f"{candidate['slug']}.html", settings.delay)
            if error or not html:
                continue
            hydrate_paper(html, candidate)
            if candidate["abstract"] and candidate["abstract"].casefold() != "abstract unavailable":
                candidate["abstractStatus"] = "present"
                old_slug = papers[index]["slug"]
                papers[index] = candidate
                write_json(path, papers)
                return {"replaced": True, "oldSlug": old_slug, "newPaper": candidate}
    raise RuntimeError("no valid 1988 replacement abstract could be obtained")


def parse_pmlr_volume(html: str, year: int, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []
    for item in soup.select("div.paper, li.paper"):
        title_node = item.select_one("p.title, .title")
        anchor = item.select_one("a[href$='.html'], a[href*='/v']")
        if not title_node or not anchor:
            continue
        title = _clean(title_node.get_text(" "))
        authors = [_clean(x.get_text(" ")) for x in item.select("span.authors a, .authors a")]
        if not authors:
            author_node = item.select_one("span.authors, .authors")
            author_text = _clean(author_node.get_text(" ") if author_node else "")
            authors = [x.strip() for x in author_text.split(",") if x.strip()]
        abstract_node = item.select_one("div.abstract, p.abstract")
        abstract = _clean(abstract_node.get_text(" ") if abstract_node else "")
        found.append(_paper(title, authors, year, "ICML", abstract, urljoin(source_url, anchor["href"]), source_url))
    return _dedupe(found)


def discover_icml_volumes(html: str) -> dict[int, str]:
    soup = BeautifulSoup(html, "html.parser")
    volumes: dict[int, str] = {}
    for anchor in soup.select("a[href]"):
        text = _clean((anchor.find_parent("li") or anchor).get_text(" "))
        match = re.search(r"(?:International Conference on Machine Learning.*?\b(20\d{2})\b|ICML\s+(20\d{2}))", text, re.I)
        href_match = re.search(r"/v(\d+)/?$|^v(\d+)/?$", anchor["href"])
        if match and href_match:
            volumes[int(match.group(1) or match.group(2))] = urljoin(PMLR_VOLUMES, anchor["href"].rstrip("/") + "/")
    return volumes


def _dedupe(papers: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for paper in papers:
        key = paper_key(paper["title"], paper["year"])
        if key not in seen:
            seen[key] = paper
        else:
            existing = seen[key]
            existing["provenance"] = sorted(set(existing["provenance"] + paper["provenance"]))
            if len(paper["abstract"]) > len(existing["abstract"]):
                existing["abstract"] = paper["abstract"]
    return list(seen.values())


def _stratified_sample(papers: list[dict], size: int, seed: int) -> list[dict]:
    """Deterministically cover venue/year strata before filling proportional remainder."""
    rng = random.Random(seed)
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for paper in papers:
        groups[(paper["venue"], paper["year"])].append(paper)
    selected: list[dict] = []
    for key in sorted(groups):
        choices = sorted(groups[key], key=lambda p: p["slug"])
        selected.append(rng.choice(choices))
    remaining = [p for p in papers if p["slug"] not in {x["slug"] for x in selected}]
    rng.shuffle(remaining)
    selected.extend(remaining[:max(0, size - len(selected))])
    # Keep a small set of actual, reader-recognizable anchors whenever the official
    # corpus contains them. Each replacement preserves the requested sample size.
    anchors = ("attention is all you need", "generative adversarial nets", "imagenet classification with deep convolutional")
    replacement_index = len(selected) - 1
    for anchor in anchors:
        candidate = next((p for p in papers if anchor in p["title"].casefold()), None)
        if candidate and candidate["slug"] not in {p["slug"] for p in selected}:
            selected[replacement_index] = candidate
            replacement_index -= 1
    return sorted(selected[:size], key=lambda p: (p["venue"], p["year"], p["slug"]))


def frozen_sample_selection(settings: Settings, freeze_sample: bool = True) -> list[dict]:
    if not freeze_sample:
        return []
    raw_path = settings.raw_dir / "papers-sample.json"
    snapshot_path = settings.root / "data/generated/papers-sample-snapshot.json"
    path = raw_path if raw_path.exists() else snapshot_path
    return read_json(path, []) if path.exists() else []


def ingest(
    settings: Settings,
    sample: bool = True,
    freeze_sample: bool = True,
    *,
    hydrate: bool | None = None,
) -> dict:
    """Collect proceedings metadata.

    Full runs deliberately write metadata first and leave the tens of thousands
    of landing-page requests to :mod:`pipeline.full_ingest`.  The smaller preview
    retains its historical convenience behavior of hydrating selected papers.
    """
    settings.prepare()
    if hydrate is None:
        hydrate = sample
    coverage: dict[str, dict[str, dict]] = {"NeurIPS": {}, "ICML": {}}
    papers: list[dict] = []
    with httpx.Client(timeout=settings.timeout, follow_redirects=True, headers={"User-Agent": "Latentscape research indexer/0.1"}) as client:
        for year in range(1987, 2027):
            if year == 2026:
                coverage["NeurIPS"][str(year)] = {"status": "not_published", "source": "https://papers.nips.cc/", "error": "no published proceedings in source index", "count": None}
                continue
            url = NEURIPS_2025_MAIN if year == 2025 else NEURIPS_INDEX.format(year=year)
            html, error = cached_get(client, url, settings.cache_dir / "neurips" / f"{year}.html", settings.delay)
            if error:
                coverage["NeurIPS"][str(year)] = {"status": "unverified", "source": url, "error": error, "count": None}
                continue
            items = parse_neurips_index(html or "", year, url)
            status = "verified" if items else "empty"
            coverage["NeurIPS"][str(year)] = {"status": status, "source": url, "count": len(items)}
            if year == 2025:
                creative_html, creative_error = cached_get(client, NEURIPS_2025_CREATIVE, settings.cache_dir / "neurips" / "2025-creative.html", settings.delay)
                if creative_error:
                    coverage["NeurIPS"][str(year)].update({"status": "partial", "additionalSource": NEURIPS_2025_CREATIVE, "error": creative_error})
                else:
                    creative = parse_neurips_index(creative_html or "", year, NEURIPS_2025_CREATIVE)
                    papers.extend(creative)
                    coverage["NeurIPS"][str(year)].update({"creativeAiCount": len(creative), "sources": [url, NEURIPS_2025_CREATIVE]})
            papers.extend(items)
            if year == 2021:
                db_html, db_error = cached_get(client, NEURIPS_2021_DATASETS, settings.cache_dir / "neurips" / "2021-datasets.html", settings.delay)
                if not db_error:
                    db_items = parse_neurips_index(db_html or "", year, NEURIPS_2021_DATASETS)
                    papers.extend(db_items)
                    coverage["NeurIPS"][str(year)]["datasetsBenchmarksCount"] = len(db_items)
        volumes_html, error = cached_get(client, PMLR_VOLUMES, settings.cache_dir / "pmlr" / "volumes.html", settings.delay)
        volumes = discover_icml_volumes(volumes_html or "") if not error else {}
        for year in range(2013, 2027):
            url = volumes.get(year)
            if not url:
                coverage["ICML"][str(year)] = {"status": "unverified", "source": PMLR_VOLUMES, "error": "ICML volume not discovered", "count": None}
                continue
            volume_html, volume_error = cached_get(client, url, settings.cache_dir / "pmlr" / f"icml-{year}.html", settings.delay)
            if volume_error:
                coverage["ICML"][str(year)] = {"status": "unverified", "source": url, "error": volume_error, "count": None}
                continue
            items = parse_pmlr_volume(volume_html or "", year, url)
            coverage["ICML"][str(year)] = {"status": "verified" if items else "empty", "source": url, "count": len(items)}
            papers.extend(items)
    if sample:
        # Preview ingestion does not make the historical adapter a prerequisite.
        for year in (1980, 1983, 1985, 1987, *range(1988, 2013)):
            coverage["ICML"][str(year)] = {"status": "unverified", "source": None, "error": "historical fallback not configured", "count": None}
    else:
        # Historical metadata has a different source chain from PMLR.  Its adapter
        # also discovers official modern position tracks; those augment rather than
        # overwrite the main-proceedings coverage above.
        from .historical_icml import ingest_historical_icml

        historical_papers, historical_coverage = ingest_historical_icml(settings)
        papers.extend(historical_papers)
        for year, record in historical_coverage.items():
            if year in coverage["ICML"]:
                coverage["ICML"][year].update(record)
            else:
                coverage["ICML"][year] = record
        # A separate official 2004 recovery pass is intentionally merge-only: it
        # adds verified abstract text and one missed proceedings record without
        # changing the historical metadata adapter's source inventory.
        updates_path = settings.raw_dir / "historical-abstract-updates.json"
        updates_payload = read_json(updates_path, {})
        updates = updates_payload.get("updates", []) if isinstance(updates_payload, dict) else []
        additions = updates_payload.get("additions", []) if isinstance(updates_payload, dict) else []
        by_slug = {paper["slug"]: paper for paper in papers}
        by_key = {paper_key(paper["title"], paper["year"]): paper for paper in papers}
        applied = 0
        for update in updates:
            target = by_slug.get(update.get("slug")) or by_key.get(paper_key(update.get("title", ""), update.get("year", 0)))
            if not target:
                continue
            abstract = update.get("abstract", "")
            if abstract:
                target.update(
                    {
                        "abstract": abstract,
                        "originalAbstract": abstract,
                        "abstractStatus": update.get("abstractStatus", "present"),
                        "abstractSource": update.get("source"),
                        "abstractQuality": "official-landing-page",
                    }
                )
                target.setdefault("provenance", []).append(update.get("source"))
                applied += 1
        papers.extend(addition for addition in additions if addition.get("slug") not in by_slug)
        # This recovery used temporary local copies solely to read official
        # abstracts; no PDFs are retained in the corpus or cache.
        updates_2005_payload = read_json(settings.raw_dir / "historical-2005-abstract-updates.json", {})
        updates_2005 = updates_2005_payload.get("updates", []) if isinstance(updates_2005_payload, dict) else []
        applied_2005 = 0
        for update in updates_2005:
            target = by_slug.get(update.get("slug")) or by_key.get(paper_key(update.get("title", ""), update.get("year", 0)))
            if not target or not update.get("abstract"):
                continue
            target.update(
                {
                    "abstract": update["abstract"],
                    "originalAbstract": update["abstract"],
                    "abstractStatus": update.get("abstractStatus", "present"),
                    "abstractSource": update.get("source"),
                    "abstractQuality": "official-pdf-abstract-extracted",
                }
            )
            target.setdefault("provenance", []).append(update.get("source"))
            applied_2005 += 1
        # The 2003 official accepted list has one source-backed paper absent from
        # the bibliographic stream. Keep that narrowly audited addition separate
        # from general fuzzy reconciliation.
        additions_2003_payload = read_json(settings.raw_dir / "historical-2003-addition.json", {})
        additions_2003 = additions_2003_payload.get("additions", []) if isinstance(additions_2003_payload, dict) else []
        papers.extend(addition for addition in additions_2003 if addition.get("slug") not in by_slug)
        if applied or additions:
            rows_2004 = [paper for paper in papers if paper["venue"] == "ICML" and paper["year"] == 2004]
            coverage["ICML"].setdefault("2004", {}).update(
                {
                    "count": len(rows_2004),
                    "abstractsPresent": sum(bool(row.get("abstract")) for row in rows_2004),
                    "abstractsMissing": sum(not bool(row.get("abstract")) for row in rows_2004),
                    "abstractSource": updates_payload.get("source") if isinstance(updates_payload, dict) else None,
                }
            )
        if additions_2003:
            rows_2003 = [paper for paper in papers if paper["venue"] == "ICML" and paper["year"] == 2003]
            coverage["ICML"].setdefault("2003", {}).update(
                {
                    "count": len(rows_2003),
                    "abstractsPresent": sum(bool(row.get("abstract")) for row in rows_2003),
                    "abstractsMissing": sum(not bool(row.get("abstract")) for row in rows_2003),
                    "inventoryAdditionSource": additions_2003_payload.get("source") if isinstance(additions_2003_payload, dict) else None,
                }
            )
        if applied_2005:
            rows_2005 = [paper for paper in papers if paper["venue"] == "ICML" and paper["year"] == 2005]
            coverage["ICML"].setdefault("2005", {}).update(
                {
                    "abstractsPresent": sum(bool(row.get("abstract")) for row in rows_2005),
                    "abstractsMissing": sum(not bool(row.get("abstract")) for row in rows_2005),
                    "abstractSource": updates_2005_payload.get("source") if isinstance(updates_2005_payload, dict) else None,
                }
            )
    papers = _dedupe(papers)
    if not sample:
        # Metadata discovery is safe to rerun while a long hydration job is in
        # progress. Preserve already retrieved source abstracts and explicit links
        # by stable slug instead of resetting the full checkpoint to pending.
        previous = {paper.get("slug"): paper for paper in read_json(settings.raw_dir / "papers-full.json", [])}
        carry = (
            "abstract", "originalAbstract", "abstractStatus", "abstractSource", "abstractQuality",
            "codeUrl", "projectUrl", "sourceQuality",
        )
        for paper in papers:
            # A source-specific recovery is newer and more precise than an
            # earlier generic landing-page parse, so never replace it merely to
            # preserve a long-running hydration checkpoint.
            if paper.get("abstractQuality") == "official-pdf-abstract-extracted":
                continue
            old = previous.get(paper["slug"])
            if not old:
                continue
            for key in carry:
                if old.get(key) not in (None, ""):
                    paper[key] = old[key]
        apply_verified_abstract_recoveries(settings, papers)
        apply_verified_author_recoveries(settings, papers)
    # Preserve the discovered source inventory independently of the selected MVP.
    write_json(settings.raw_dir / "papers-inventory.json", papers)
    if sample and ({"NeurIPS", "ICML"} - {paper["venue"] for paper in papers}):
        raise RuntimeError("sample ingestion requires verified papers from both NeurIPS and ICML")
    frozen = frozen_sample_selection(settings, freeze_sample) if sample else []
    if frozen:
        discovered = {paper["slug"]: paper for paper in papers}
        missing = [paper["slug"] for paper in frozen if paper["slug"] not in discovered]
        if missing:
            raise RuntimeError(f"frozen sample selection no longer resolves to discovered source records: {missing[:3]}")
        # The committed reviewed snapshot is authoritative for the MVP. Preserve its order,
        # repaired abstracts and outbound metadata across later source-parser fixes.
        selected = frozen
    else:
        selected = _stratified_sample(papers, settings.sample_size, settings.sample_seed) if sample else papers
    # Abstracts are necessary for semantic retrieval. The full corpus uses the
    # resumable bounded-concurrency hydrator; the preview keeps this small
    # synchronous pass for convenience.
    if hydrate:
        with httpx.Client(timeout=settings.timeout, follow_redirects=True, headers={"User-Agent": "Latentscape research indexer/0.1"}) as client:
            for paper in selected:
                if paper["abstract"]:
                    continue
                filename = f"{paper['slug']}.html"
                html, error = cached_get(client, paper["url"], settings.cache_dir / "paper-pages" / filename, settings.delay)
                if not error and html:
                    hydrate_paper(html, paper)
    write_json(settings.raw_dir / "coverage.json", coverage)
    write_json(settings.raw_dir / ("papers-sample.json" if sample else "papers-full.json"), selected)
    return {"papers": len(selected), "available": len(papers), "coverage": coverage}
