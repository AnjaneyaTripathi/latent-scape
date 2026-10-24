# Summary provenance

This is maintainer documentation for the paper-explanation data contract.

Paper explanations are generated once from the recorded source abstract, then validated before export. The reader sees the unchanged source abstract first, followed by a TL;DR and three to five explanatory paragraphs. A source-abstract hash binds an accepted explanation to the text used to author it.

Every rendered TL;DR and explanation must display **AI-generated summary** next to an original-paper link. The explanation is a navigation aid, not a source; readers should use the outbound proceedings, OpenReview, or arXiv link for the original work.

Working directories, logs, schemas, and response diagnostics are internal build material. They must not be exported to `site/public/` or included in the published static artifact. Final summary shards may be retained for reproducible offline builds, provided their abstract hashes remain valid.
