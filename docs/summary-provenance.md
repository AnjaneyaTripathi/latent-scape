# Summary provenance

Paper summaries in the review sample are one-time, agent-authored Astra outputs requested for this project. They are generated offline and stored with each paper's `summary.source` and `summary.model` fields. The browser only reads the resulting static JSON.

Every rendered summary and TLDR must display **AI-generated summary** beside the original-paper link. The summary is a navigation aid, not a source: readers should follow the outbound proceedings, OpenReview, or arXiv link for the original work.

The 200-paper review gate is retained as a checkpointed sample and includes validation against the public data schema. No external inference API was invoked. Agent model usage is not measured or costed by the pipeline. `config.toml` stays untracked; `config.example.toml` only documents optional provider settings for a future locally authorized rerun.
