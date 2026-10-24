# Latentscape static-data contract

All browser-consumed data is generated before deployment. The site must never call a
research, embedding, or LLM API at runtime.

`site/public/data/manifest.json` is the entry point:

```json
{
  "snapshot": "2026-10-23",
  "totalPapers": 500,
  "totalTopics": 0,
  "shards": {"papers": {"ab": "papers/ab.json"}, "vectors": "vectors.json"}
}
```

`search.json` is an array of lightweight documents with `id`, `slug`, `type`
(`paper` or `topic`), `title`, `abstract`/`description`, original `url` for papers, `venue`, `year`, and
`topicSlugs`. It is sufficient for keyword search and its ordering fixes the vector
row order for paper documents.

Paper search documents also carry optional `tldr`, copied exactly and in full from
the published paper's `summary.tldr` when it is a nonempty string. It is absent
when no published TLDR exists. Search cards display this complete TLDR; their
480-character `abstract` field is only for indexing. Full abstracts and summary
paragraphs remain in paper shards and load when a reader opens the paper details.
`scripts/build-search-summaries.mjs` refreshes only this projection offline during
prebuild, preserving document order and vector alignment. It never authors prose
or modifies paper shards, and keeps `search.json` below the 100 MB asset limit.

`topics.json` is an array. Each topic contains reader-facing fields only:
`slug`, `title`, `area`, `broadGroupId`, `overview`, `whyItMatters`, `evolution`,
`openQuestions`, `prerequisites`, `difficulty`, `paperSlugs`, and `relatedTopics`.

Papers live in `papers/<first two characters of SHA-256(slug)>.json`; each shard is
an array. A paper has `slug`, `title`, `authors`, `year`, `venue`, `abstract`, `url`,
optional `codeUrl`/`projectUrl`, nullable `citationCount`, `references`, `citedBy`,
`similarPapers`, `topicSlugs`, and nullable `summary`. A summary has its TLDR,
reader-facing explanation paragraphs, and optional difficulty. Editorial provenance,
source hashes, model identifiers, and review state never cross this boundary.

Vectors use bounded binary shards, avoiding JSON bloat and GitHub Pages' 100 MB file
limit. `vectors.json` stores `{dimensions, scale: 127, shards: [{path, ids}]}`. Each
`.i8` shard contains contiguous normalized signed int8 rows in `ids` order. Every
shard must remain below 16 MB. The browser dequantizes components by dividing by
`scale` and lazy-loads shards.

`topic-graph.json` (schema version 2) is a compact, precomputed semantic
neighborhood for every topic. First-degree cards contain the complete direct
union: the root topic's outgoing ranked semantic neighbors first, then
incoming-only neighbors in deterministic slug order; duplicates and self-links
are excluded. Second-degree cards contain every known distance-two neighbor of
those cards after every root-adjacent topic is excluded. An edge is included only when either
endpoint records that relationship. Each neighborhood has fixed-width (1040px),
per-neighborhood bounds with a minimum 900px height; positions are for
navigation and do not imply a spatial similarity metric.

`topic-papers/<topic-slug>.json` is a complete, paginatable paper list for one
topic. It includes the topic's reader-facing context, named related topics and
prerequisites, and one compact row for every `paperSlugs` member in that exact
order. Rows include source-link metadata and an existing TLDR when available;
they intentionally omit abstracts and explanatory paragraphs.

Coverage audits, source-recovery records, release measurements, and summary-review
artifacts are retained outside `site/public` for maintainers. They are intentionally
not part of the browser contract.
