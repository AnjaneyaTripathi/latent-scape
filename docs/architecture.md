# Static architecture

This is maintainer documentation.

The offline Python pipeline writes a constrained reader-facing data set into `site/public/data`. Vite copies that data, the locally vendored MiniLM ONNX model, and the ONNX WASM runtime into the static artifact. The deployed application has no server, database, authentication, or runtime research API calls.

The browser loads the application shell and lightweight navigation data first. Search lazily loads a keyword index and starts semantic search in a worker. The worker runs `Xenova/all-MiniLM-L6-v2` locally, embeds a query, and compares it with quantized vectors generated from the same model. Paper detail shards load only when a reader opens a paper.

The knowledge map reads `topic-graph.json`, a precomputed focused semantic
neighbourhood for each topic. First-degree cards are the source topic's ranked
semantic neighbours; selected second-degree cards are known neighbours of those
cards. Stored card positions avoid a runtime layout, and lines are emitted only
for an existing semantic relation (never an inferred citation or similarity).

Each topic also has a static `topic-papers/<slug>.json` list. It carries the
topic context and compact metadata for every member paper, so pagination does
not require loading the all-topic index or full paper-detail shards.

`scripts/validate-artifacts.py` is the release boundary. It rejects PDFs, remote model or runtime assets, oversized artifacts, and internal audit or review material in the published bundle. Build measurements and maintainer provenance stay outside the reader-facing artifact.
