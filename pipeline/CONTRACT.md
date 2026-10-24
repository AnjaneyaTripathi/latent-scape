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

`topic-graph.json` is a compact, precomputed semantic neighborhood for every topic.
The first-degree cards are the root topic's outgoing ranked semantic neighbors.
Selected second-degree cards are known neighbors of those cards, and an edge is
included only when either endpoint records that relationship. Its fixed card
positions are for navigation; they do not imply a spatial similarity metric.

Coverage audits, source-recovery records, release measurements, and summary-review
artifacts are retained outside `site/public` for maintainers. They are intentionally
not part of the browser contract.
