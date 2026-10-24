# Maintaining Latentscape

This guide is for maintainers of the frozen static snapshot. It is not part of the reader-facing site.

## Principles

- Keep the source snapshot immutable unless deliberately creating a new, documented snapshot revision.
- Never commit credentials. Copy `config.example.toml` to `config.toml` for local settings only.
- Do not retain or publish paper PDFs. Keep outbound links only.
- Export only validated summaries whose source-abstract hashes match.
- Keep build diagnostics, review material, logs, and source audits outside `site/public/`.

## Rebuild static assets

Requires Node 22 or later. From the repository root:

```sh
cd site
npm ci
npm run build
cd ..
python3 scripts/write-artifact-report.py site/dist
python3 scripts/validate-artifacts.py site/dist
```

The build uses committed static inputs. It does not fetch research data or call a language model.

## Re-run pipeline stages

Use `uv run` for pipeline commands. Run stages only when intentionally updating the snapshot or repairing source evidence:

| Stage | Command |
| --- | --- |
| Official metadata | `uv run latentscape ingest-full` |
| Abstract recovery | `uv run latentscape hydrate-full` |
| Source checks | `uv run latentscape audit-full-abstracts` and `uv run latentscape extract-full-outbound-links` |
| Citation enrichment | `uv run latentscape enrich --corpus full --config config.toml` |
| Embeddings and topics | `uv run python -m pipeline.full_build prepare` |
| Paper explanations | `uv run python -m pipeline.full_build generate` |
| Topic-guide import | `uv run latentscape import-topics --corpus full` |
| Static derivation | `uv run latentscape similarity --corpus full`, `uv run latentscape glossary --corpus full`, and `uv run latentscape export --corpus full` |

Read [full build](full-build.md), [narrative authoring](astra-runner.md), and [summary provenance](summary-provenance.md) before changing a frozen input or summary output. Historical source repairs require a documented source revision, updated embeddings, preserved topic identities where possible, and a fresh validation pass.

## Release procedure

1. Build the static artifact and run its validator.
2. Run browser smoke tests for navigation, search, offline behavior, and mobile layouts.
3. Review the artifact boundary: no PDFs, credentials, build logs, review pages, source audits, or operational metadata may be published.
4. Push release-ready changes to `main`. The **Build and deploy static site** workflow builds and validates the committed inputs, then automatically deploys the artifact to GitHub Pages only if the build and validation succeed. Collection and authoring never run in that workflow.

Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** once. To rebuild manually, run **Build and deploy static site** from the Actions tab; check `deploy` to publish that run, or leave it unchecked for a build-only run.
