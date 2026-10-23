# Data sources and coverage policy

Latentscape's source of record for NeurIPS is the [NeurIPS Proceedings](https://proceedings.neurips.cc/), indexed one year at a time at `https://proceedings.neurips.cc/paper/<year>`. Its source of record for current ICML proceedings is [Proceedings of Machine Learning Research](https://proceedings.mlr.press/), whose volume index links to the ICML volumes.

The ingestion stage saves every response before parsing it and writes a per-year audit to `coverage.json`. A year is `verified` only when its source was fetched and parsed. An empty successful page is `empty`; a missing volume, request failure, or unconfigured historical source is `unverified`. The build never turns an unavailable source into a verified zero.

The frozen 2026-10-23 inventory contains 54,437 records: 30,840 NeurIPS and
23,597 ICML. It is a one-time snapshot, not a live feed or a claim that every
record has completed editorial curation. Historical ICML metadata is merged from
documented source-specific adapters; ICML 1980 and 1985 remain explicit
unverified gaps. Individual missing abstracts or author blocks are retained as
unavailable rather than invented. Temporary official PDF inspection may be used
only to recover an explicit abstract section; PDFs are never retained or shipped.
The [earliest-proceedings audit](../data/generated/earliest-proceedings-audit.json)
records why 1980 has no formal proceedings inventory and why the original 1985
workshop inventory remains unverified. The [collision audit](../data/generated/source-collision-audit.json)
and its reviewed source-correction artifact document the later abstract repairs;
they do not claim complete coverage of all historical workshop material.

Citation metadata may be matched offline from OpenAlex or Semantic Scholar during enrichment. These are enrichment sources, not replacements for proceedings metadata. The published graph keeps only NeurIPS/ICML-to-NeurIPS/ICML edges while retaining global citation counts when matched.

Paper pages link out to the official proceedings, OpenReview, or arXiv source recorded for the work. An outbound original-paper URL may itself be a PDF, but Latentscape never copies, serves, or hosts that PDF.
