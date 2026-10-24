#!/usr/bin/env python3
"""Render the one-time 200-paper Astra summary sample as a local review page."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


REQUIRED = ("tldr", "difficulty", "source", "model")


def text(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def narrative_paragraphs(summary: dict) -> list[str]:
    paragraphs = summary.get("paragraphs")
    if isinstance(paragraphs, list) and 3 <= len(paragraphs) <= 5 and all(isinstance(value, str) and value.strip() for value in paragraphs):
        return paragraphs
    return [summary.get(key, "") for key in ("problem", "idea", "results", "limitations") if str(summary.get(key, "")).strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summaries", type=Path, default=Path("data/generated/summaries-astra-sample.json"))
    parser.add_argument("--papers", type=Path, default=Path("pipeline/data/derived/papers-topics-sample.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/generated/reviews/summary-review.html"),
        help="Local editorial review artifact; never place it under site/public.",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    public_root = Path("site/public").resolve()
    if output == public_root or public_root in output.parents:
        raise SystemExit("summary review is an editorial artifact and cannot be written under site/public")
    summaries = json.loads(args.summaries.read_text(encoding="utf-8"))
    papers = json.loads(args.papers.read_text(encoding="utf-8"))
    if not isinstance(summaries, dict) or len(summaries) != 200:
        raise SystemExit("summary review requires exactly 200 summaries")
    by_slug = {paper["slug"]: paper for paper in papers}
    cards: list[str] = []
    for slug, summary in sorted(summaries.items(), key=lambda item: by_slug.get(item[0], {}).get("title", item[0]).casefold()):
        if slug not in by_slug:
            raise SystemExit(f"summary references absent paper: {slug}")
        if not isinstance(summary, dict) or any(not str(summary.get(key, "")).strip() for key in REQUIRED):
            raise SystemExit(f"summary is incomplete: {slug}")
        paragraphs = narrative_paragraphs(summary)
        if not 3 <= len(paragraphs) <= 5:
            raise SystemExit(f"summary needs 3–5 explanatory paragraphs: {slug}")
        paper = by_slug[slug]
        original = text(paper.get("url"))
        cards.append(f'''<article class="summary" data-difficulty="{text(summary["difficulty"])}">
<p class="meta">{text(paper.get("venue"))} · {text(paper.get("year"))} · {text(summary["difficulty"])} <span>AI-generated summary</span></p>
<h2>{text(paper.get("title"))}</h2>
<p class="tldr"><b>TL;DR</b> {text(summary["tldr"])}</p>
<p><a href="./#/paper/{text(slug)}">Open in Latentscape</a> · <a href="{original}" target="_blank" rel="noreferrer">Original paper ↗</a></p>
<details><summary>Read the official abstract and explanation</summary>
<h3>Official abstract</h3><p>{text(paper.get("abstract"))}</p>
<h3>What this paper did</h3>{''.join(f'<p>{text(paragraph)}</p>' for paragraph in paragraphs)}
<p class="provenance">Generated offline · source: {text(summary["source"])} · model: {text(summary["model"])}</p>
</details></article>''')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Latentscape · Astra summary review</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;margin:0;background:#fffaf2;color:#201d1a}}main{{max-width:900px;margin:auto;padding:2rem 1.25rem 4rem}}h1{{font-size:clamp(2rem,6vw,4rem);line-height:1;margin:.1rem 0}}.lede,.provenance{{color:#625b51}}.summary{{background:#fff;border:1px solid #ded5c8;border-radius:12px;margin:1rem 0;padding:1.25rem}}.meta{{font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;color:#625b51;margin:0}}.meta span{{color:#a33b1d;font-weight:700}}h2{{font-size:1.2rem;line-height:1.25;margin:.45rem 0}}a{{color:#9b3519}}.tldr{{border-left:3px solid #e65322;padding-left:.8rem}}summary{{cursor:pointer;font-weight:700}}dl{{display:grid;grid-template-columns:8rem 1fr;gap:.45rem 1rem}}dt{{font-weight:700}}dd{{margin:0}}@media(max-width:560px){{dl{{display:block}}dt{{margin-top:1rem}}}}</style></head><body><main>
<p><a href="./#/about">← Latentscape</a></p><h1>200-paper summary review</h1><p class="lede">One-time Astra-authored offline sample. Use your browser’s Find command to search all titles and summary text. Each entry links to its original paper; no PDF is hosted here.</p>
<p class="lede">{len(cards)} summaries · static local artifact · no runtime API</p>{''.join(cards)}</main></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    print(f"wrote {output} with {len(cards)} summaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
