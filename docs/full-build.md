# Full-corpus build

This is maintainer documentation for the one-time offline pipeline. GitHub Pages receives only exported static files; it never runs collection, enrichment, or summary authoring.

Use the commands from [the maintenance guide](maintaining.md) for the normal stage order. The Python entry points are available when a scripted invocation is useful:

```sh
python -m pipeline.full_build prepare
python -m pipeline.full_build status
python -m pipeline.full_build generate
python -m pipeline.full_build finalize
```

`prepare` resumes hash-keyed embedding and topic-preparation checkpoints. `generate` consumes the frozen eligible-summary input and writes validated summary shards. `finalize` imports only schema-valid, abstract-hash-matched summaries, builds similarity and glossary data, exports browser data, and runs the static build and artifact validation.

For a complete unattended run, use:

```sh
python -m pipeline.full_build run
```

The run is resumable. Its input selection, ordering, validation revision, and exclusion snapshot remain fixed so a restart cannot silently change the corpus being authored. Records without sufficient source text remain unavailable; the pipeline never generates title-only explanations.
