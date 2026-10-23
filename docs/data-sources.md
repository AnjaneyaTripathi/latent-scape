# Data sources and coverage policy

Latentscape's source of record for NeurIPS is the [NeurIPS Proceedings](https://proceedings.neurips.cc/), indexed one year at a time at `https://proceedings.neurips.cc/paper/<year>`. Its source of record for current ICML proceedings is [Proceedings of Machine Learning Research](https://proceedings.mlr.press/), whose volume index links to the ICML volumes.

The ingestion stage saves every response before parsing it and writes a per-year audit to `coverage.json`. A year is `verified` only when its source was fetched and parsed. An empty successful page is `empty`; a missing volume, request failure, or unconfigured historical source is `unverified`. The build never turns an unavailable source into a verified zero.

The present MVP deliberately uses a deterministic, stratified 500-paper preview. It is not a claim of complete NeurIPS or ICML coverage. The PMLR adapter can parse ICML volume pages from 2013 onward when they are discoverable, but the frozen preview's verified ICML inventory currently covers 2017–2026; 2013–2016 and earlier years remain explicitly unverified. The preview samples NeurIPS 1988–2025. Its 1987 source record remains in the audit, but was not sampled because it lacks a usable official abstract.

Citation metadata may be matched offline from OpenAlex or Semantic Scholar during enrichment. These are enrichment sources, not replacements for proceedings metadata. The published graph keeps only NeurIPS/ICML-to-NeurIPS/ICML edges while retaining global citation counts when matched.

Paper pages link out to the official proceedings, OpenReview, or arXiv source recorded for the work. Latentscape never copies or serves paper PDFs.
