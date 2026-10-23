# Latentscape static-data contract

All browser-consumed data is generated before deployment. The site must never call a
research, embedding, or LLM API at runtime.

`site/public/data/manifest.json` is the entry point:

```json
{
  "snapshot": "2026-10-23",
  "totalPapers": 500,
  "totalTopics": 0,
  "coverage": {"NeurIPS": {}, "ICML": {}},
  "shards": {"papers": {"ab": "papers/ab.json"}, "vectors": "vectors.json"}
}
```

`search.json` is an array of lightweight documents with `id`, `slug`, `type`
(`paper` or `topic`), `title`, `abstract`/`description`, original `url` for papers, `venue`, `year`, and
`topicSlugs`. It is sufficient for keyword search and its ordering fixes the vector
row order for paper documents.

`topics.json` is an array. Each topic contains `slug`, `title`, `area`, `overview`,
`whyItMatters`, `evolution`, `openQuestions`, `prerequisites`, `difficulty`,
`paperSlugs`, and `relatedTopics`.

Papers live in `papers/<first two characters of SHA-256(slug)>.json`; each shard is
an array. A paper has `slug`, `title`, `authors`, `year`, `venue`, `abstract`, `url`,
optional `codeUrl`/`projectUrl`, nullable `citationCount`, `references`, `citedBy`,
`similarPapers`, `topicSlugs`, and nullable `summary`. A summary has `problem`,
`idea`, `results`, `limitations`, `tldr`, `difficulty`, `source`, and `model`.

Vectors use bounded binary shards, avoiding JSON bloat and GitHub Pages' 100 MB file
limit. `vectors.json` stores `{dimensions, scale: 127, shards: [{path, ids}]}`. Each
`.i8` shard contains contiguous normalized signed int8 rows in `ids` order. Every
shard must remain below 16 MB. The browser dequantizes components by dividing by
`scale` and lazy-loads shards.

`coverage.json` is an audit: every requested venue/year is `verified`, `empty`, or
`unverified`, includes source URLs and paper counts, and explicitly records known
coverage holes. Counts must never be represented as verified zero merely because a
source request failed.
