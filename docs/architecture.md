# Static architecture

The offline Python pipeline writes browser data into `site/public/data`. Vite copies those files, the locally vendored MiniLM ONNX model, and the ONNX WASM runtime into the Pages artifact. The deployed application has no server, database, authentication, or runtime API calls.

The browser first loads a compact keyword index, then starts semantic search lazily in a worker. The worker runs `Xenova/all-MiniLM-L6-v2` locally, embeds the query, and compares it with quantized vectors generated from the same model. Paper detail shards load only when a reader opens a paper.

`scripts/validate-artifacts.py` inspects the final artifact. It rejects PDFs, remote model/runtime assets, files over GitHub Pages' decimal 100 MB limit, sites over its 1 GB limit, and missing data/model/schema/report files. `data/report.json` records the artifact size and corpus counts for release review.
