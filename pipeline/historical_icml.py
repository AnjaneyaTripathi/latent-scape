"""Historical ICML metadata, official position tracks, and source-backed recovery.

DBLP's documented public SPARQL service supplies proceedings metadata. Conference
HTML supplies abstracts; the official virtual site's own JSON supplies position
papers. Missing abstracts remain empty. Successful responses are resumably cached.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from .ingest import _clean, _paper, extract_outbound_links
from .settings import Settings, load_settings
from .util import paper_key, read_json, write_json

SPARQL = "https://sparql.dblp.org/sparql"
QUERY = """PREFIX dblp: <https://dblp.org/rdf/schema#>
SELECT ?pub ?title ?year ?authorName ?ordinal ?doi ?document WHERE {
 ?pub a dblp:Inproceedings;
  dblp:publishedInStream <https://dblp.org/streams/conf/icml>;
  dblp:publishedAsPartOf ?book; dblp:title ?title; dblp:yearOfPublication ?year .
 FILTER (STR(?year) < "2013" &&
  STR(?book) = CONCAT("https://dblp.org/rec/conf/icml/", STR(?year)))
 OPTIONAL { ?sig a dblp:AuthorSignature; dblp:signaturePublication ?pub;
  dblp:signatureDblpName ?authorName; dblp:signatureOrdinal ?ordinal . }
 OPTIONAL { ?pub dblp:doi ?doi }
 OPTIONAL { ?pub dblp:documentPage ?document }
} ORDER BY ?year ?pub ?ordinal"""
OFFICIAL = {
    2008: "https://icml.cc/Conferences/2008/abstracts.shtml.html",
    2009: "https://icml.cc/2009/abstracts.html",
    2010: "https://icml.cc/2010/abstracts.html",
    2011: "https://icml.cc/2011/papers.php.html",
    2012: "https://icml.cc/2012/papers/",
}
CROSSREF_1987 = "https://api.crossref.org/works?filter=isbn:9780934613415&rows=1000"
# Reviewed author-identical title changes between the conference and bibliography.
TITLE_EQUIVALENTS = {
    (
        2008,
        "Fast Estimation of Relational Pattern Coverage through Randomization "
        "and Maximum Likelihood",
    ): "conf/icml/KuzelkaZ08",
    (
        2010,
        "A theoretical analysis of feature pooling in vision algorithms",
    ): "conf/icml/BoureauPL10",
}
# Visually verified against the organizer's proceedings TOC, PDF pages 4-6
# (printed v-vii), and author index page 7. Only bibliographic facts retained.
TOC_1983 = [
    (2, "Learning by Augmenting Rules and Accumulating Censors", ["Patrick H. Winston"]),
    (
        12,
        "Derivational Analogy in Problem Solving and Knowledge Acquisition",
        ["Jaime G. Carbonell"],
    ),
    (
        19,
        "Concept Formation by Incremental Analogical Reasoning and Debugging",
        ["Mark H. Burstein"],
    ),
    (26, "Programming by Analogy", ["Nachum Dershowitz"]),
    (32, "Reasoning by Analogy in Scientific Theory Construction", ["Lindley Darden"]),
    (
        41,
        "Discovering Patterns in Sequences of Objects",
        ["Thomas G. Dietterich", "Ryszard S. Michalski"],
    ),
    (58, "Learning from Noisy Data", ["J. R. Quinlan"]),
    (65, "Inductive Rule Generation in the Context of the Fifth Generation", ["Donald Michie"]),
    (71, "Knowledge Acquisition and Learning in EXPERT", ["Casimir A. Kulikowski"]),
    (74, "Hierarchical Memories: An Aid to Concept Learning", ["Claude Sammut", "Ranan Banerji"]),
    (
        81,
        "Learning as a Non-Deterministic But Exact Logical Process",
        ["Yves Kodratoff", "J.-G. Ganascia"],
    ),
    (92, "Escaping Brittleness", ["John H. Holland"]),
    (
        96,
        "Two Programs for Testing Hypotheses of Any Logical Form",
        ["Clark Glymour", "Kevin Kelly", "Richard Scheines"],
    ),
    (99, "Learning Equation Solving Methods from Worked Examples", ["Bernard Silver"]),
    (105, "Adjusting Bias in Concept Learning", ["Paul E. Utgoff"]),
    (110, "Operationalizing Advice: A Problem-Solving Model", ["Jack Mostow"]),
    (117, "Goal Directed Learning", ["Tom M. Mitchell", "Richard M. Keller"]),
    (
        119,
        "Program Synthesis as a Theory Formation Task: Problem Representations and Problem Methods",
        ["Saul Amarel"],
    ),
    (
        121,
        "Mechanisms for Qualitative and Quantitative Discovery",
        ["Pat Langley", "Jan Zytkow", "Herbert A. Simon", "Gary L. Bradshaw"],
    ),
    (
        133,
        "Cognitive Economy in a Fluid Task Environment",
        ["Douglas B. Lenat", "Frederick Hayes-Roth", "Philip Klahr"],
    ),
    (
        147,
        "The Role of Experimentation in Theory Formation",
        ["Thomas G. Dietterich", "Bruce G. Buchanan"],
    ),
    (156, "How to Structure Structured Objects", ["Ryszard S. Michalski", "Robert E. Stepp"]),
    (161, "The Architecture of Jumbo", ["Douglas R. Hofstadter"]),
    (171, "An Approach to Learning From Observation", ["Gerald DeJong"]),
    (177, "Concept Learning in a Rich Input Domain", ["Michael Lebowitz"]),
    (
        183,
        "The Chunking of Goal Hierarchies: A Generalized Model of Practice",
        ["Paul S. Rosenbloom", "Allen Newell"],
    ),
    (
        198,
        "Learning Physical Domains: Towards a Theoretical Framework",
        ["Kenneth D. Forbus", "Dedre Gentner"],
    ),
    (203, "Knowledge Compilation: The General Learning Mechanism", ["John R. Anderson"]),
    (213, "Linear Separability and Concept Naturalness", ["Douglas L. Medin"]),
    (
        218,
        "How Can CHILD Learn About Agreement? Explorations of CHILD's Syntactic Inadequacies",
        ["Mallory Selfridge"],
    ),
    (221, "Inferring (MAL) Rules from Pupil's Protocols", ["Derek Sleeman"]),
    (228, "Domain-specific Learning and the Subset Principle", ["Robert C. Berwick"]),
    (234, "Validating a Theory of Human Skill Acquisition", ["Kurt VanLehn"]),
]


def _fetch(client: httpx.Client, url: str, path: Path) -> str:
    if path.exists():
        text = path.read_text()
        if "Making sure you're not a bot" not in text:
            return text
    response = client.get(url)
    response.raise_for_status()
    if response.status_code != 200 or "Making sure you" in response.text:
        raise ValueError(f"Source did not return public metadata: HTTP {response.status_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text)
    return response.text


def parse_dblp(payload: dict) -> list[dict]:
    rows: dict[str, dict] = {}
    authors: dict[str, dict[int, str]] = defaultdict(dict)
    for binding in payload["results"]["bindings"]:
        values = {key: value["value"] for key, value in binding.items()}
        uri, year = values["pub"], int(values["year"])
        if uri not in rows:
            rows[uri] = _paper(
                values["title"].rstrip("."),
                [],
                year,
                "ICML",
                "",
                uri,
                f"https://dblp.org/db/conf/icml/icml{year}.html",
            )
            rows[uri].update(
                {
                    "dblpId": uri.rsplit("/rec/", 1)[-1],
                    "track": "main",
                    "abstractStatus": "missing",
                    "metadataSource": SPARQL,
                    "sourceUrls": [],
                }
            )
        paper = rows[uri]
        if values.get("authorName"):
            authors[uri][int(values["ordinal"])] = re.sub(r" \d{4}$", "", values["authorName"])
        if values.get("doi"):
            paper["doi"] = values["doi"].removeprefix("https://doi.org/")
        if values.get("document") and values["document"] not in paper["sourceUrls"]:
            paper["sourceUrls"].append(values["document"])
    for uri, paper in rows.items():
        paper["authors"] = [name for _, name in sorted(authors[uri].items())]
    return list(rows.values())


def _authors(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)", "", text)
    return [x.strip() for x in re.split(r";|,|\s+and\s+", text) if x.strip()]


def parse_official(html: str, year: int, source: str) -> tuple[list[dict], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    papers, excluded = [], []
    if year == 2012:
        candidates = []
        for block in soup.select("div.paper"):
            title = _clean(block.h2.get_text(" "))
            kind_node = block.select_one(".type")
            # Legacy HTML omits </p>; get only the status text before descendants.
            kind = _clean(next(kind_node.strings, "")) if kind_node else ""
            if "Not for proceedings" in kind:
                excluded.append({"title": title, "reason": kind})
                continue
            author_node = block.select_one(".authors")
            author_text = _clean(next(author_node.strings, "")) if author_node else ""
            abstract_node = block.select_one(".abstract")
            abstract_parts = []
            if abstract_node:
                for child in abstract_node.children:
                    if getattr(child, "name", None) == "p":
                        break
                    if getattr(child, "name", None) != "strong":
                        abstract_parts.append(
                            child.get_text(" ") if hasattr(child, "get_text") else str(child)
                        )
            candidates.append(
                (
                    title,
                    author_text,
                    _clean(" ".join(abstract_parts)),
                    block.get("id", ""),
                    kind,
                    block,
                )
            )
    elif year == 2011:
        candidates = []
        for anchor in soup.select("a[name]"):
            heading = anchor.find("h3", recursive=False)
            if not heading:
                continue
            title = _clean(heading.get_text(" "))
            if not anchor["name"].isdigit():
                if anchor["name"].startswith("X"):
                    excluded.append({"title": title, "reason": "cross-conference paper"})
                continue
            author = anchor.find("span", class_="name", recursive=False)
            abstract = anchor.find("p", recursive=False)
            candidates.append(
                (
                    title,
                    _clean(author.get_text(" ")) if author else "",
                    re.sub(r"^Abstract:\s*", "", _clean(abstract.get_text(" ")))
                    if abstract
                    else "",
                    anchor["name"],
                    "main",
                    anchor,
                )
            )
    else:
        candidates = []
        headings = list(re.finditer(r"<h3\b[^>]*>(.*?)</h3>", html, re.I | re.S))
        for index, match in enumerate(headings):
            heading = BeautifulSoup(match.group(0), "html.parser")
            stop = headings[index + 1].start() if index + 1 < len(headings) else len(html)
            block = BeautifulSoup(
                re.split(r"<hr\b", html[match.end() : stop], maxsplit=1, flags=re.I)[0],
                "html.parser",
            )
            author = block.select_one("i, em")
            if not author:
                continue
            title = _clean(heading.get_text(" "))
            abstract_node = block.select_one("p.abstracts")
            if abstract_node is None:
                paragraphs = [
                    p
                    for p in block.find_all("p", recursive=False)
                    if _clean(p.get_text(" ")) != _clean(author.get_text(" "))
                    and not p.find("a", string=re.compile(r"Full [pP]aper"))
                    and not _clean(p.get_text()).lower().startswith("paper id:")
                ]
                abstract = _clean(" ".join(p.get_text(" ") for p in paragraphs))
            else:
                abstract = _clean(abstract_node.get_text(" "))
            if "removed from the ICML" in abstract:
                excluded.append({"title": title, "reason": abstract})
                continue
            anchor = heading.select_one("a[name]")
            if anchor is None:
                preceding = BeautifulSoup(
                    html[max(0, match.start() - 150) : match.start()], "html.parser"
                )
                anchor = preceding.select_one("a[name]")
            identifier = anchor.get("name", "") if anchor else ""
            candidates.append((title, author.get_text(" "), abstract, identifier, "main", block))
    for title, author, abstract, identifier, kind, block in candidates:
        paper = _paper(
            title,
            _authors(author),
            year,
            "ICML",
            abstract,
            source + (f"#{identifier}" if identifier else ""),
            source,
        )
        paper.update(
            {
                "track": "invited-applications" if "Invited applications" in kind else "main",
                "abstractStatus": "present" if abstract else "missing",
                "sourceUrls": [
                    urljoin(source, a["href"])
                    for a in block.select("a[href]")
                    if ".pdf" in a["href"]
                ],
            }
        )
        papers.append(paper)
    return list({paper_key(p["title"], year): p for p in papers}.values()), excluded


def parse_positions(payload: dict, year: int, source: str) -> list[dict]:
    papers = {}
    for row in payload.get("results", []):
        position_source = "Position_Paper_Track" in (row.get("sourceurl") or "")
        # 2024 position submissions used the single Conference group; the official
        # required "Position:" title identifies their track in that year's feed.
        position_2024 = year == 2024 and row.get("name", "").startswith("Position:")
        paper_url = (
            row.get("paper_url")
            or row.get("paper_pdf_url")
            or urljoin("https://icml.cc", row.get("virtualsite_url") or "")
        )
        if not (position_source or position_2024) or not paper_url:
            continue
        if "accept" not in (row.get("decision") or "").lower():
            continue
        paper = _paper(
            row["name"],
            [a["fullname"] for a in row.get("authors", [])],
            year,
            "ICML",
            row.get("abstract") or "",
            paper_url,
            source,
        )
        paper.update(
            {
                "track": "position",
                "abstractStatus": "present" if paper["abstract"] else "missing",
                "sourceUrls": [urljoin("https://icml.cc", row["virtualsite_url"])],
            }
        )
        if "openreview.net/forum?id=" in paper_url:
            paper["openReviewId"] = paper_url.split("id=")[-1]
        paper["codeUrl"], paper["projectUrl"] = extract_outbound_links(paper["abstract"])
        key = paper_key(paper["title"], year)
        existing = papers.get(key)
        if existing is None or (row.get("paper_url") and not existing.get("openReviewId")):
            papers[key] = paper
    return list(papers.values())


def parse_1987(payload: dict) -> list[dict]:
    papers = []
    for row in payload["message"]["items"]:
        if row.get("type") != "book-chapter" or not row.get("author"):
            continue
        # Publisher metadata attaches a funding footnote to this one title.
        title = row["title"][0].split("11Support for this research was provided")[0]
        paper = _paper(
            title,
            [_clean(a.get("given", "") + " " + a.get("family", "")) for a in row["author"]],
            1987,
            "ICML",
            "",
            "https://doi.org/" + row["DOI"],
            CROSSREF_1987,
        )
        paper.update(
            {
                "doi": row["DOI"],
                "track": "main",
                "abstractStatus": "missing",
                "pages": row.get("page"),
                "publisher": row.get("publisher"),
            }
        )
        papers.append(paper)
    return papers


def recover_pre2008_abstracts(settings: Settings) -> dict:
    """Write a separate reviewable patch set; never mutate the frozen full corpus."""
    cache = settings.cache_dir / "historical"
    inventory = read_json(settings.raw_dir / "papers-historical-icml.json", [])
    updates, additions, unmatched, errors = [], [], [], []
    with httpx.Client(timeout=settings.timeout, follow_redirects=True) as client:
        source = "https://icml.cc/Conferences/2004/proceedings.html"
        html = _fetch(client, source, cache / "icml-2004-abstract-probe.html")
        soup = BeautifulSoup(html, "html.parser")
        links = sorted(
            {
                urljoin(source, a["href"])
                for a in soup.select("a[href]")
                if "Abstract" in a.get_text()
            }
        )
        by_title = {paper_key(p["title"], 2004): p for p in inventory if p["year"] == 2004}

        def read_abstract(url):
            try:
                page = _fetch(client, url, cache / "abstracts-2004" / url.rsplit("/", 1)[-1])
                doc = BeautifulSoup(page, "html.parser")
                heading, cells = doc.select_one("th"), doc.select("td")
                if not heading or len(cells) < 2:
                    return {"url": url, "error": "Unrecognized official abstract table"}
                return {
                    "url": url,
                    "title": _clean(heading.get_text(" ")),
                    "abstract": _clean(cells[-1].get_text(" ")),
                }
            except Exception as exc:
                return {"url": url, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(read_abstract, links))
        for row in results:
            if row.get("error"):
                errors.append(row)
                continue
            equivalent_titles = {
                "161.htm": "Online and batch learning of pseudo-metrics",
                "335.htm": "Apprenticeship learning via inverse reinforcement learning",
                "364.htm": "Decentralized detection and classification using kernel methods",
                "391.htm": "Learning random walk models for inducing word dependency distributions",
            }
            match_title = equivalent_titles.get(row["url"].rsplit("/", 1)[-1], row["title"])
            paper = by_title.get(paper_key(match_title, 2004))
            if paper:
                updates.append(
                    {
                        "slug": paper["slug"],
                        "title": paper["title"],
                        "year": 2004,
                        "abstract": row["abstract"],
                        "abstractStatus": "present",
                        "source": row["url"],
                    }
                )
            elif row["url"].endswith("/354.htm"):
                paper = _paper(
                    row["title"], ["Andrew Ng"], 2004, "ICML", row["abstract"], row["url"], source
                )
                paper.update(
                    {
                        "track": "main",
                        "abstractStatus": "present",
                        "provenance": [source, row["url"]],
                    }
                )
                additions.append(paper)
            else:
                unmatched.append(row)
    result = {
        "updates": updates,
        "additions": additions,
        "unmatched": unmatched,
        "errors": errors,
        "source": source,
        "sourceRecordCount": len(links),
    }
    write_json(settings.raw_dir / "historical-abstract-updates.json", result)
    return result


def recover_2005_abstracts(settings: Settings) -> dict:
    """Read official first pages temporarily; retain only bounded abstract metadata."""
    cache = settings.cache_dir / "historical"
    source = "https://icml.cc/Conferences/2005/proceedings.html"
    inventory = read_json(settings.raw_dir / "papers-historical-icml.json", [])
    by_title = {paper_key(p["title"], 2005): p for p in inventory if p["year"] == 2005}
    with httpx.Client(timeout=max(45, settings.timeout), follow_redirects=True) as client:
        html = _fetch(client, source, cache / "icml-2005-abstract-probe.html")
        soup = BeautifulSoup(html, "html.parser")
        entries = [
            (_clean(a.get_text(" ")), urljoin(source, a["href"]))
            for a in soup.select("a[href]")
            if re.search(r"\.pdff?$", a["href"])
        ]

        def recover(entry):
            title, url = entry
            path = cache / "abstracts-2005" / (url.rsplit("/", 1)[-1] + ".json")
            if path.exists():
                return read_json(path)
            try:
                response = client.get(url)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Official link did not return a PDF")
                with tempfile.TemporaryDirectory(
                    prefix="latentscape-abstract-", dir="/private/tmp"
                ) as temporary:
                    pdf = Path(temporary) / "paper.pdf"
                    pdf.write_bytes(response.content)
                    text = subprocess.run(
                        ["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                        check=True,
                        text=True,
                        capture_output=True,
                    ).stdout
                boundaries = {
                    "080_Recycling_OntanonPlaza.pdf": r"1\. Motivation:",
                    "087_Healing_RasmussenQuinoneroCandela.pdf": r"Bayesian inference based on",
                    "112_TDLambdaNetworks_TannerSutton.pdf": (
                        r"Temporal-difference " r"\(TD\) networks are"
                    ),
                }
                end = boundaries.get(url.rsplit("/", 1)[-1], r"1\.?\s+Introduction\b")
                match = re.search(r"\nAbstract\s*\n(.*?)\n\s*" + end, text, re.S | re.I)
                if not match:
                    raise ValueError(
                        "First page lacks unambiguous Abstract-to-Introduction boundary"
                    )
                abstract = _clean(match.group(1))
                if not 200 <= len(abstract) <= 3500:
                    raise ValueError("Extracted abstract outside reviewable length bounds")
                row = {
                    "title": title,
                    "abstract": abstract,
                    "source": url,
                    "abstractExtraction": "pdftotext first page; explicit section boundaries",
                }
                write_json(path, row)
                return row
            except Exception as exc:
                return {"title": title, "source": url, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(recover, entries))
    updates, unmatched, errors = [], [], []
    for row in rows:
        if row.get("error"):
            errors.append(row)
            continue
        equivalents = dict(
            [
                (
                    "Intrinsic Dimensionality Estimation of Submanifolds in (d",
                    "Intrinsic dimensionality estimation of submanifolds in Rd",
                ),
                (
                    "Adapting Two-Class Classification Methods to Many Class Problems",
                    "Adapting two-class support vector classification methods "
                    "to many class problems",
                ),
                (
                    "Multi-Class protein fold detection using adaptive codes",
                    "Multi-class protein fold recognition using adaptive codes",
                ),
                (
                    "TDLambda Networks: Temporal-Difference Networks with Eligibility Traces",
                    "TD(lambda) networks: temporal-difference networks with eligibility traces",
                ),
            ]
        )
        row["abstract"] = row["abstract"].split("Appearing in Proceedings", 1)[0].strip()
        # Each repair below was checked against a rendered original first page.
        filename = row["source"].rsplit("/", 1)[-1]
        if filename == "007_Error_BeygelzimerEtAl.pdf":
            row["abstract"] = row["abstract"].replace("\x02", "ϵ")
        elif filename == "037_Intrinsic_HeinAudibert.pdf":
            row["abstract"] = row["abstract"].split("audibert@", 1)[0].strip()
            row["abstract"] = (
                row["abstract"].replace("in Rd", "in ℝᵈ").replace("U -statistic", "U-statistic")
            )
        elif filename == "120_ExploitingSyntactic_WangEtAl.pdf":
            row["abstract"] = row["abstract"].replace("\x02", "n")
            row["abstract"] = (
                row["abstract"].replace("n -gram", "n-gram").replace("n gram", "n-gram")
            )
        if re.search(r"@|[\x00-\x08\x0b\x0c\x0e-\x1f]|�", row["abstract"]):
            errors.append(
                {
                    "source": row["source"],
                    "title": row["title"],
                    "error": "Abstract text mapping requires visual review",
                }
            )
            continue
        paper = by_title.get(paper_key(equivalents.get(row["title"], row["title"]), 2005))
        if paper:
            updates.append(
                {**row, "slug": paper["slug"], "year": 2005, "abstractStatus": "present"}
            )
        else:
            unmatched.append(row)
    result = {
        "updates": updates,
        "unmatched": unmatched,
        "errors": errors,
        "source": source,
        "sourceRecordCount": len(entries),
    }
    write_json(settings.raw_dir / "historical-2005-abstract-updates.json", result)
    return result


def ingest_historical_icml(settings: Settings) -> tuple[list[dict], dict[str, dict]]:
    settings.prepare()
    cache = settings.cache_dir / "historical"
    coverage: dict[str, dict] = {}
    errors = []
    excluded = []
    papers = []
    with httpx.Client(
        timeout=max(settings.timeout, 90),
        follow_redirects=True,
        headers={"User-Agent": "Latentscape research metadata indexer/0.1"},
    ) as client:
        try:
            text = _fetch(
                client,
                SPARQL + "?" + urlencode({"query": QUERY, "format": "json"}),
                cache / "dblp-main-historical.json",
            )
            papers = parse_dblp(json.loads(text))
            # These are proceedings front/back matter, not research contributions.
            ancillary = [
                p
                for p in papers
                if re.match(
                    r"(?:Workshop summary:|Tutorial summary:|Invited talk:)", p["title"], re.I
                )
            ]
            excluded.extend(
                {
                    "year": p["year"],
                    "title": p["title"],
                    "reason": "Proceedings event summary, not a research paper",
                }
                for p in ancillary
            )
            papers = [p for p in papers if p not in ancillary]
        except Exception as exc:
            errors.append({"source": SPARQL, "error": str(exc)})
        for year in range(1988, 2013):
            rows = [p for p in papers if p["year"] == year]
            coverage[str(year)] = {
                "status": "verified" if rows else "unverified",
                "count": len(rows) if rows else None,
                "source": f"https://dblp.org/db/conf/icml/icml{year}.html",
                "metadataSource": SPARQL,
                "scope": "published proceedings",
            }
        for year, source in OFFICIAL.items():
            try:
                html = _fetch(client, source, cache / f"icml-{year}.html")
                official, omitted = parse_official(html, year, source)
                excluded.extend({"year": year, **x} for x in omitted)
                exclusion_keys = {paper_key(x["title"], year) for x in omitted}
                papers = [
                    p for p in papers if paper_key(p["title"], p["year"]) not in exclusion_keys
                ]
                year_rows = {paper_key(p["title"], year): p for p in papers if p["year"] == year}
                for row in official:
                    key = paper_key(row["title"], year)
                    existing = year_rows.get(key)
                    if existing is None and (year, row["title"]) in TITLE_EQUIVALENTS:
                        existing = next(
                            (
                                p
                                for p in year_rows.values()
                                if p.get("dblpId") == TITLE_EQUIVALENTS[(year, row["title"])]
                            ),
                            None,
                        )
                        if existing:
                            existing["sourceTitleVariants"] = [row["title"]]
                    if existing is None:
                        # Conference and bibliography titles occasionally differ;
                        # exact official PDF identifier resolves those editions.
                        links = {
                            u.replace("http:", "https:").removesuffix(".html")
                            for u in row["sourceUrls"]
                        }
                        existing = next(
                            (
                                p
                                for p in year_rows.values()
                                if links
                                & {
                                    u.replace("http:", "https:").removesuffix(".html")
                                    for u in p.get("sourceUrls", [])
                                }
                            ),
                            None,
                        )
                    if existing is None:
                        # Metadata typography/diacritics and expanded math notation
                        # differ between archives. Require both a very close title
                        # and matching author surnames, and record the reconciliation.
                        def normalize(value):
                            return re.sub(
                                r"[^a-z0-9]",
                                "",
                                unicodedata.normalize("NFKD", value)
                                .encode("ascii", "ignore")
                                .decode()
                                .lower(),
                            )

                        surnames = {normalize(a.split()[-1]) for a in row["authors"] if a.split()}
                        matches = [
                            p
                            for p in year_rows.values()
                            if SequenceMatcher(
                                None, normalize(p["title"]), normalize(row["title"])
                            ).ratio()
                            > 0.86
                            and surnames
                            & {normalize(a.split()[-1]) for a in p["authors"] if a.split()}
                        ]
                        if len(matches) == 1:
                            existing = matches[0]
                            existing["sourceTitleVariants"] = [row["title"]]
                    if existing:
                        existing.update(
                            {
                                "abstract": row["abstract"],
                                "abstractStatus": row["abstractStatus"],
                                "url": row["url"],
                                "track": row["track"],
                            }
                        )
                        existing["provenance"].append(source)
                    else:
                        papers.append(row)
                coverage[str(year)]["abstractSource"] = source
                coverage[str(year)]["officialRecordCount"] = len(official)
                coverage[str(year)]["excludedCount"] = len(omitted)
            except Exception as exc:
                errors.append({"source": source, "error": str(exc)})
                coverage[str(year)]["abstractError"] = str(exc)
        for year in (1980, 1983, 1985, 1987):
            coverage[str(year)] = {
                "status": "unverified",
                "count": None,
                "source": "https://icml.cc/Conferences/2007/pastconferences.html",
                "error": (
                    "Early workshop exists; complete metadata inventory unresolved. "
                    "DBLP ICML stream begins in 1988."
                ),
            }
        coverage["1980"]["investigatedSources"] = ["https://www.mli.gmu.edu/cgi-bin/pubs.cgi"]
        coverage["1983"]["investigatedSources"] = [
            "https://www.mli.gmu.edu/pubs.html",
            "https://www.mli.gmu.edu/papers/other/ML-83.pdf",
        ]
        source_1983 = "https://www.mli.gmu.edu/papers/other/ML-83.pdf"
        for page, title, authors in TOC_1983:
            paper = _paper(
                title, authors, 1983, "ICML", "", f"{source_1983}#page={page + 6}", source_1983
            )
            paper.update(
                {
                    "track": "main",
                    "abstractStatus": "missing",
                    "startPage": page,
                    "metadataVerification": "Visually reviewed proceedings TOC pages v-vii",
                }
            )
            papers.append(paper)
        coverage["1983"] = {
            "status": "verified",
            "count": len(TOC_1983),
            "source": source_1983,
            "abstractsPresent": 0,
            "abstractsMissing": len(TOC_1983),
            "scope": "Complete organizer proceedings TOC; author index excluded",
            "verification": "33 titles/authors visually checked against PDF pages 4-6",
        }
        coverage["1985"]["investigatedSources"] = [
            "https://www.mli.gmu.edu/cgi-bin/pubs.cgi",
            "https://www.cs.utexas.edu/~ai-lab/pub-list-date.php",
        ]
        try:
            rows = parse_1987(
                json.loads(_fetch(client, CROSSREF_1987, cache / "icml-1987-crossref.json"))
            )
            papers.extend(rows)
            coverage["1987"] = {
                "status": "verified",
                "count": len(rows),
                "source": CROSSREF_1987,
                "abstractsPresent": 0,
                "abstractsMissing": len(rows),
                "scope": (
                    "Publisher-registered authored proceedings chapters; "
                    "front matter and index excluded"
                ),
                "publisherSource": "https://www.sciencedirect.com/book/edited-volume/9780934613415/proceedings-of-the-fourth-international-workshop-on-machine-learning",
            }
        except Exception as exc:
            coverage["1987"]["error"] = str(exc)
            errors.append({"source": CROSSREF_1987, "error": str(exc)})
        for year in (2024, 2025, 2026):
            source = f"https://icml.cc/static/virtual/data/icml-{year}-orals-posters.json"
            try:
                payload = json.loads(
                    _fetch(client, source, cache / f"position-{year}-inventory.json")
                )
                rows = parse_positions(payload, year, source)
                papers.extend(rows)
                coverage[str(year)] = {
                    "positionTracks": {
                        "status": "verified" if rows else "unverified",
                        "count": len(rows),
                        "source": source,
                        "scope": "accepted position papers in official public conference feed",
                    }
                }
            except Exception as exc:
                coverage[str(year)] = {
                    "positionTracks": {
                        "status": "unverified",
                        "count": None,
                        "source": source,
                        "error": str(exc),
                    }
                }
                errors.append({"source": source, "error": str(exc)})
    for year in range(1988, 2013):
        rows = [p for p in papers if p["year"] == year]
        coverage[str(year)]["count"] = len(rows) if rows else None
        coverage[str(year)]["abstractsPresent"] = sum(bool(p["abstract"]) for p in rows)
        coverage[str(year)]["abstractsMissing"] = sum(not p["abstract"] for p in rows)
    audit = {
        "snapshotDate": settings.snapshot_date,
        "paperCount": len(papers),
        "coverage": coverage,
        "excluded": excluded,
        "errors": errors,
        "completeHistoricalCoverage": False,
        "sourcePolicy": (
            "Public metadata; earliest proceedings temporarily read for TOC verification. "
            "No hosted PDFs, invented abstracts or challenge bypass."
        ),
        "dblpQuery": QUERY,
        "dblpServiceDocumentation": (
            "https://blog.dblp.org/2024/09/09/introducing-our-public-sparql-query-service/"
        ),
    }
    write_json(settings.raw_dir / "papers-historical-icml.json", papers)
    write_json(settings.root / "data/generated/historical-audit.json", audit)
    return papers, coverage


if __name__ == "__main__":
    records, result = ingest_historical_icml(load_settings(Path.cwd()))
    print(
        json.dumps(
            {
                "papers": len(records),
                "years": dict(Counter(p["year"] for p in records)),
                "abstracts": sum(bool(p["abstract"]) for p in records),
                "coverage": result,
            },
            indent=2,
        )
    )


def recover_2007_abstracts(settings: Settings) -> dict:
    """Read the preserved official ICML 2007 first-page left column, temporarily."""
    source = "https://icml.cc/Conferences/2007/paperlist.html"
    cache = settings.cache_dir / "historical"
    inventory = read_json(settings.raw_dir / "papers-historical-icml.json", [])
    by_title = {paper_key(p["title"], 2007): p for p in inventory if p["year"] == 2007}
    equivalents = {
        "Focussed Crawling with Scalable Ordinal Regression Solvers": (
            "Focused crawling with scalable ordinal regression solvers"
        ),
        "Feature Selection in Kernel Space": "Feature selection in a kernel space",
        "Learning to Compress Images and Video": "Learning to compress images and videos",
        "Kernel Selection for Semi-Supervised Kernel Machines": (
            "Kernel selection forl semi-supervised kernel machines"
        ),
        "Percentile Optimization in Uncertain MDP with Application to Efficient Exploration": (
            "Percentile optimization in uncertain Markov decision processes "
            "with application to efficient exploration"
        ),
        "The Hierarchical Gaussian Process Latent Variable Model": (
            "Hierarchical Gaussian process latent variable models"
        ),
        "Revisiting Probabilistic Models for Clustering with Constraints": (
            "Revisiting probabilistic models for clustering with pair-wise constraints"
        ),
        "Discriminative Gaussian Process Latent Variable Models for Classification": (
            "Discriminative Gaussian process latent variable model for classification"
        ),
        "Spectral Clustering with Multiple Views": (
            "Spectral clustering and transductive learning with multiple views"
        ),
    }
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        html = _fetch(client, source, cache / "icml-2007-abstract-probe.html")
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        for anchor in soup.select("a[href]"):
            if not re.search(r"/papers/\d+\.pdf$", anchor["href"]):
                continue
            title = anchor.find_previous("a", attrs={"name": True}).get_text(" ", strip=True)
            url = (
                "https://icml.cc/Conferences/2007/proceedings/papers/"
                + anchor["href"].rsplit("/", 1)[-1]
            )
            entries.append((title, url))

        def recover(entry):
            title, url = entry
            paper = by_title.get(paper_key(equivalents.get(title, title), 2007))
            row = {
                "title": title,
                "source": url,
                "year": 2007,
                "slug": paper["slug"] if paper else None,
            }
            path = cache / "abstracts-2007-column" / (url.rsplit("/", 1)[-1] + ".json")
            if path.exists():
                return read_json(path)
            try:
                if not paper:
                    raise ValueError("Title requires bibliography reconciliation")
                response = client.get(url)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Official archive did not return a PDF")
                with tempfile.TemporaryDirectory(
                    prefix="icml2007-", dir="/private/tmp"
                ) as temporary:
                    pdf = Path(temporary) / "paper.pdf"
                    pdf.write_bytes(response.content)
                    text = subprocess.run(
                        [
                            "pdftotext",
                            "-f",
                            "1",
                            "-l",
                            "1",
                            "-x",
                            "0",
                            "-y",
                            "100",
                            "-W",
                            "305",
                            "-H",
                            "650",
                            str(pdf),
                            "-",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                    review = Path("/private/tmp/icml2007-review")
                    review.mkdir(exist_ok=True)
                    (review / (url.rsplit("/", 1)[-1] + ".txt")).write_text(text)
                match = re.search(
                    r"\nAbstract\s*\n(.*?)\n\s*1\.?\s+Introduction\b", text, re.S | re.I
                )
                if not match:
                    raise ValueError("No explicit Abstract-to-Introduction boundary")
                abstract = _clean(match.group(1))
                if not 200 <= len(abstract) <= 3500:
                    raise ValueError("Extracted text outside review bounds")
                if re.search(r"@|[\x00-\x08\x0b\x0c\x0e-\x1f]|�", abstract):
                    raise ValueError("PDF character mapping requires visual review")
                if not abstract.endswith((".", "?", "!")):
                    raise ValueError("Abstract ending requires visual review")
                row.update(
                    abstract=abstract,
                    abstractStatus="present",
                    abstractExtraction=(
                        "Official PDF first page left column; explicit section boundaries"
                    ),
                )
            except Exception as exc:
                row["error"] = str(exc)
            write_json(path, row)
            return row

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(recover, entries))
    result = {
        "updates": [r for r in rows if not r.get("error")],
        "errors": [r for r in rows if r.get("error")],
        "sourceRecordCount": len(entries),
        "source": source,
    }
    write_json(settings.raw_dir / "historical-2007-abstract-updates.json", result)
    return result
