# Data sources and coverage policy

This is maintainer documentation for the frozen source snapshot.

Latentscape's source of record for NeurIPS is the [NeurIPS Proceedings](https://proceedings.neurips.cc/). Its source of record for current ICML proceedings is [Proceedings of Machine Learning Research](https://proceedings.mlr.press/), whose volume index links to the ICML volumes.

The pipeline saves source responses before parsing and records per-year verification results. A year is marked verified only after a source has been fetched and parsed; an unavailable source is never converted into a verified zero. The frozen 2026-10-23 inventory contains 54,437 records: 30,840 NeurIPS and 23,597 ICML.

Historical ICML metadata combines documented source-specific adapters. The 1980 and 1985 workshop inventories remain incomplete. Missing abstracts and author blocks stay unavailable rather than being invented. Temporary inspection of an official PDF may recover an explicit abstract section, but PDFs are never retained or shipped.

OpenAlex or Semantic Scholar may provide offline citation enrichment. These are enrichment sources, not replacements for proceedings metadata. Paper pages link out to an official proceedings, OpenReview, or arXiv source when one is recorded. Latentscape never copies, serves, or hosts a PDF.
