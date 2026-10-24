# Latentscape

Latentscape is a static guide to machine-learning research from NeurIPS and ICML. It helps readers start with an idea, understand the surrounding concepts, and reach the original paper when they want the technical detail.

## Explore the snapshot

The site offers these ways into the collection:

- **Search** accepts plain-language queries and returns relevant papers and topics.
- **Topic pages** explain an idea, its development, related concepts, and a selection of early and recent papers.
- **Knowledge map** starts from a topic and shows its closest semantic neighbours and selected second-degree connections.
- **Paper pages** show the unchanged source abstract when available, followed by a clearly labelled AI-generated TL;DR and plain-language explanation. Each page includes the available original-source or publication-record link.

The fixed snapshot is dated **2026-10-23** and contains **54,437 papers** (30,840 NeurIPS and 23,597 ICML), organized into **256 topics** across 32 broad areas. It includes **52,836 AI-generated paper explanations** grounded in the available source abstracts.

Latentscape does not host papers or PDFs. It links to the official proceedings, OpenReview, arXiv, code repositories, and project pages when those links are known. Readers can revisit loaded pages offline; search works offline after its local search resources have loaded. Original-paper links require an internet connection.

## What to keep in mind

AI-generated explanations are reading aids, not replacements for the original work. The source link stays visible on every paper page, and the original abstract is shown when it is available.

Some historical records do not have enough source text for an explanation; the site marks those cases rather than inventing a summary. Coverage of the early ICML 1980 and 1985 workshops is incomplete. This is a one-time snapshot, not a live feed.

## Run locally

Requires Node 22 or later.

```sh
cd site
npm ci
npm run build
npm run preview
```

Open the local address printed by Vite. To develop with live reload instead, run `npm run dev` from `site/`.

## For maintainers

The application is fully static: it has no server, accounts, database, or runtime research API. Its offline pipeline lives in `pipeline/`, and the web application lives in `site/`.

For frozen-snapshot rules, rebuilding stages, configuration, validation, and manual release instructions, read [the maintenance guide](docs/maintaining.md). The supporting technical references are [architecture](docs/architecture.md), [data sources](docs/data-sources.md), and [summary provenance](docs/summary-provenance.md).
