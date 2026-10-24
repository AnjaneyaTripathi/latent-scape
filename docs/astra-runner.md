# Narrative authoring runner

This maintainer tool creates resumable, abstract-grounded paper explanations with the locally authenticated Codex model configured by the project. It is an offline authoring step: the browser receives only validated static JSON and does not call a model or API.

The runner accepts records with a paper slug, title, abstract, and optional source-abstract hash. It writes validated summary maps at the output directory's top level. Checkpoints, schemas, diagnostics, and manifests stay in `working/`, where import discovery cannot mistake them for reader-facing data.

For the frozen proceedings run, use:

```sh
python -m pipeline.full_build generate
```

For a separate sample or review run:

```sh
python -m pipeline.astra_runner \
  --input /path/to/records.json \
  --output-dir /path/to/new-run \
  --dry-run
```

Remove `--dry-run` after inspecting the manifest. The runner batches records, can resume completed batches, and rejects output with an incorrect abstract hash, unsupported topic slug, invalid schema, or an explanation outside the required three-to-five paragraph contract. It never generates an explanation for missing or placeholder source text.

Only one runner may own an output directory. If the frozen input, model, batch size, or validation revision changes, use a new output directory. Keep `config.toml` local and untracked; never place credentials in generated output or version control.
