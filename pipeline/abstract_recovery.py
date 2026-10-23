"""Recover malformed NeurIPS abstracts from the publisher's original first pages.

PDFs are temporary working inputs, never corpus assets. Only bounded abstract text
and source/boundary evidence are retained. Uncertain extraction stays unresolved.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .settings import load_settings
from .util import read_json, write_json

MIN_WORDS = 40
MAX_CHARS = 3500


def normalize(value: str) -> str:
    # Remove only layout line breaks; preserve printed punctuation and spelling.
    value = re.sub(r"(?<=\w)-\n\s*(?=[a-z])", "", value)
    return " ".join(value.split())


def extract_pdf_abstract(text: str, title: str = "") -> tuple[str, str]:
    """Use explicit Abstract heading and section/typographic boundary on page one."""
    if title == "Gaussian Fields for Approximate Inference in Layered Sigmoid Belief Networks":
        if "Correctness of belief propagation" in text:
            raise ValueError("Official archive PDF contains a different paper (Weiss/Freeman)")
    if title == "Approximately Efficient Online Mechanism Design":
        text = text.replace("\x0f", "ϵ")  # Verified against rendered original.
    first_page = text.split("\f", 1)[0]
    lines = first_page.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.fullmatch(r"\s*ABSTRACT\s*:?\s*", line, re.I)),
        None,
    )
    if start is None:
        raise ValueError("No explicit Abstract heading on first page")
    remaining = lines[start + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    collected: list[str] = []
    blank_run = 0
    boundary = None
    # Wrapped lines establish the left edge of the abstract, excluding the first
    # paragraph's potentially indented opening line.
    first_block = []
    for line in remaining:
        if not line.strip():
            break
        first_block.append(line)
    if not first_block:
        raise ValueError("Empty abstract block")
    left = min(len(x) - len(x.lstrip()) for x in first_block)
    for line in remaining:
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            collected.append("")
            continue
        current = normalize("\n".join(collected))
        # A section heading must stand alone; do not split ordinary prose that
        # merely contains the word introduction.
        if re.match(
            r"^(?:(?:1(?:\.|\s)|I\.)\s*[A-Z]|INTRODU(?:CTION|cnON)\s*$)",
            stripped,
            re.I,
        ):
            boundary = "explicit first-section heading"
            break
        reviewed_boundaries = {
            "Network Generality, Training Required, and Precision Required": (
                "Generality versus Training Data Required"
            ),
            "Temporal Patterns of Activity in Neural Networks": "METHODS / PRELIMINARY RESULTS",
            (
                "SPONTANEOUS AND INFORMATION-TRIGGERED SEGMENTS OF SERIES "
                "OF HUMAN BRAIN ELECTRIC FIELD MAPS"
            ): "BRAIN FUNCTIONAL STATES AND THEIR CHANGES",
            (
                "Stimulus Encoding by Multidimensional Receptive Fields in Single Cells "
                "and Cell Populations in V1 of Awake Monkey"
            ): "377",
        }
        if stripped == reviewed_boundaries.get(title):
            boundary = "reviewed original section or page boundary"
            break
        indent = len(line) - len(line.lstrip())
        if len(current.split()) >= MIN_WORDS and blank_run:
            if indent <= left - 4:
                boundary = "abstract indentation ends at body paragraph"
                break
            if blank_run >= 3:
                boundary = "large vertical gap after abstract block"
                break
        collected.append(line)
        blank_run = 0
    if boundary is None:
        raise ValueError("No unambiguous abstract end on first page")
    abstract = normalize("\n".join(collected))
    if len(abstract.split()) < 15 or len(abstract) > MAX_CHARS:
        raise ValueError("Abstract outside bounded length limits")
    if re.search(r"[\x00-\x08\x0b\x0e-\x1f]|�|\(cid:", abstract):
        raise ValueError("PDF character mapping needs visual transcription")
    if re.search(
        r"(?:@|All rights reserved|American Institute of Physics|Appearing in Proceedings)",
        abstract,
    ):
        raise ValueError("Possible author/footer material inside extracted abstract")
    if title == "Natasha 2: Faster Non-Convex Optimization Than SGD":
        abstract = re.sub(r"\.2$", ".²", abstract)
    if not abstract.endswith((".", "?", "!", ".’", '."', ")", ".²")):
        raise ValueError("Abstract end is not a complete sentence; needs visual review")
    if title.startswith("SPONTANEOUS AND INFORMATION-TRIGGERED"):
        abstract = abstract.replace("9", "g")  # Scanned glyphs visually checked.
    if title.startswith("Stimulus Encoding by Multidimensional"):
        abstract = abstract.replace("in VI of", "in V1 of")
    if title.startswith("Gaussian Fields for Approximate"):
        abstract = abstract.replace("that is,·", "that is,")
    return abstract, boundary


def candidate_papers(settings) -> list[dict]:
    papers = read_json(settings.raw_dir / "papers-full.json", [])
    candidates = []
    for paper in papers:
        if paper.get("venue") != "NeurIPS":
            continue
        path = settings.cache_dir / "paper-pages" / f"{paper['slug']}.html"
        if not path.exists():
            continue
        soup = BeautifulSoup(path.read_text(), "html.parser")
        heading = next(
            (
                h
                for h in soup.find_all(["h2", "h3", "h4"])
                if h.get_text(strip=True).casefold() == "abstract"
            ),
            None,
        )
        node = heading.find_next(["p", "div"]) if heading else None
        source_text = normalize(node.get_text(" ")) if node else ""
        if source_text and MIN_WORDS <= len(source_text.split()) and len(source_text) <= MAX_CHARS:
            continue
        link = next(
            (a for a in soup.select("a[href]") if re.search(r"-Paper\.pdf$", a["href"], re.I)), None
        )
        candidates.append(
            {
                **paper,
                "recoveryPdf": urljoin(paper["url"], link["href"]) if link else None,
                "rejectedLength": len(source_text),
                "rejectedWords": len(source_text.split()),
            }
        )
    return candidates


def recover(settings, workers: int = 4) -> dict:
    candidates = candidate_papers(settings)
    cache = settings.cache_dir / "abstract-recovery"
    cache.mkdir(parents=True, exist_ok=True)
    review = Path("/private/tmp/neurips-recovery-review")
    review.mkdir(exist_ok=True)
    print(f"Recovering {len(candidates)} malformed abstracts", flush=True)

    def one(paper):
        path = cache / f"{paper['slug']}.json"
        if path.exists():
            row = read_json(path)
            local_text = review / f"{paper['slug']}.txt"
            if row.get("extractionVersion") == 3 or not local_text.exists():
                return row
            try:
                abstract, boundary = extract_pdf_abstract(local_text.read_text(), paper["title"])
                row.pop("error", None)
                row.update(
                    abstract=abstract,
                    originalAbstract=abstract,
                    abstractStatus="present",
                    abstractSource="official-pdf-abstract-section",
                    abstractQuality="source-section-recovered",
                    abstractExtraction=boundary,
                    provenance=[paper["url"], paper["recoveryPdf"]],
                    extractionVersion=3,
                )
            except Exception as exc:
                for field in ("abstract", "originalAbstract", "abstractSource", "abstractQuality"):
                    row.pop(field, None)
                row.update(
                    error=str(exc), abstractStatus="needs-source-recovery", extractionVersion=3
                )
            write_json(path, row)
            return row
        row = {
            "slug": paper["slug"],
            "title": paper["title"],
            "year": paper["year"],
            "source": paper["recoveryPdf"],
            "extractionVersion": 3,
            "rejectedLength": paper["rejectedLength"],
        }
        try:
            if not paper["recoveryPdf"]:
                raise ValueError("No official PDF link on landing page")
            response = httpx.get(paper["recoveryPdf"], timeout=60, follow_redirects=True)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError("Official PDF link returned non-PDF data")
            with tempfile.TemporaryDirectory(
                prefix="neurips-abstract-", dir="/private/tmp"
            ) as temporary:
                pdf = Path(temporary) / "paper.pdf"
                pdf.write_bytes(response.content)
                page = 8 if paper["title"].startswith("Gaussian Fields for Approximate") else 1
                row["pdfStartPage"] = page
                text = subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                # Private review material only, never published or embedded.
                (review / f"{paper['slug']}.txt").write_text(text)
                abstract, boundary = extract_pdf_abstract(text, paper["title"])
            row.update(
                abstract=abstract,
                originalAbstract=abstract,
                abstractStatus="present",
                abstractSource="official-pdf-abstract-section",
                abstractQuality="source-section-recovered",
                abstractExtraction=boundary,
                provenance=[paper["url"], paper["recoveryPdf"]],
            )
        except Exception as exc:
            row.update(error=str(exc), abstractStatus="needs-source-recovery")
        write_json(path, row)
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, candidates))
    result = {
        "sourceRecordCount": len(candidates),
        "updates": [r for r in rows if not r.get("error")],
        "unresolved": [r for r in rows if r.get("error")],
        "sourcePolicy": (
            "Original official first-page abstract sections only; temporary PDF inputs; "
            "no generated abstracts or body text."
        ),
    }
    write_json(settings.raw_dir / "neurips-abstract-recovery.json", result)
    print(
        json.dumps(
            {
                "candidates": len(candidates),
                "updates": len(result["updates"]),
                "unresolved": len(result["unresolved"]),
            }
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    arguments = parser.parse_args()
    recover(load_settings(Path.cwd()), arguments.workers)
