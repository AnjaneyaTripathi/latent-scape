# Latentscape

Latentscape is a static, concept-first guide to NeurIPS and ICML research. It has no server, database, account system, runtime research API, or hosted PDFs. The browser loads precomputed search, graph, and paper data and links out to the official paper source.

## Current snapshot

The frozen source corpus contains **54,437 papers**: 30,840 NeurIPS and 23,597 ICML records. It contains 52,893 nonempty source abstracts, of which 52,836 currently meet the evidence gate for narrative authoring; 1,601 records are ineligible because source evidence is missing, under recovery, insufficient, or otherwise excluded by validation. Short genuine abstracts remain visible rather than being rewritten. ICML 1980 and 1985 remain explicitly unverified historical gaps. The corpus is a one-time snapshot dated 2026-10-23; it is not a live feed.

The topic pass has 32 broad areas and 256 fine topics; all current guides are carry-forward reviewed after the controlled source correction. Citation coverage is partial and Astra narrative authoring is ongoing: do not interpret the corpus count as a claim that every paper has been curated. The live authoring runner has an adjustable target of up to 32 concurrent workers; its current completion count, active batches, measured throughput, and rate-based ETA are recorded in `data/generated/narratives/full/working/progress.json`. The ETA is empirical rather than guaranteed. Deployment remains pending because this repository is private.

## Build the static site

Requires Node 22+ for this static build. Pipeline requirements and deliberate
offline stage commands are listed below.

```sh
cd site && npm ci && npm run build
```

This build consumes committed static inputs only. Offline preparation and authoring
belong to the deliberate pipeline stages below; `finalize` imports only
schema-valid, abstract-hash-matched narratives, then creates similarity,
glossary, and browser data. `--allow-incomplete` is only for a clearly labelled
private preview; it never marks the build release-ready. See [the full-build
guide](docs/full-build.md) and [Astra runner guide](docs/astra-runner.md).

Do not rerun full ingestion or hydration without deliberately replacing the frozen snapshot. The raw corpus is cache-backed and source-audited; PDFs are never retained or shipped. Copy `config.example.toml` to `config.toml` only for local settings, and never commit keys.

## Rerun stages deliberately

Use `uv run` for every pipeline command; `uv sync` alone does not activate the environment. These commands either resume the current cached run or deliberately begin a new source snapshot:

| Stage | Command | Scope |
| --- | --- | --- |
| Official metadata | `uv run latentscape ingest-full` | New/changed source snapshot only; preserve the frozen snapshot otherwise. |
| Abstract recovery | `uv run latentscape hydrate-full` | Resumes cached official-page hydration. |
| Source QA | `uv run latentscape audit-full-abstracts` and `uv run latentscape extract-full-outbound-links` | Rechecks cached source evidence and explicit links. |
| Citation enrichment | `uv run latentscape enrich --corpus full --config config.toml` | Offline cache-backed enrichment when configured, then merge its approved results. |
| Embeddings/topics | `uv run python -m pipeline.full_build prepare` | Resumes hash-bound embedding chunks and topic preparation. |
| Astra narratives | `uv run python -m pipeline.full_build generate --workers 24` | Resumes the frozen eligible input; choose 1–32 workers from measured throughput and account capacity. |
| Guides/import | `uv run latentscape import-topics --corpus full` | Imports only approved, hash-matched guide shards. |
| Static derivation | `uv run latentscape similarity --corpus full`, `uv run latentscape glossary --corpus full`, `uv run latentscape export --corpus full` | Rebuilds static navigation and site data. |

Historical abstract repairs are source-specific, reviewed patches. Restore or replace them only as a new source revision with its evidence/audit, corrected embeddings, preserved topic IDs, and explicit Astra guide approval; do not edit a frozen raw file by hand.

## Data and verification

`pipeline/` owns offline collection, enrichment, embedding, topic construction, narrative validation, and export. `site/` is the static Vite application. `site/public/data/manifest.json` is the browser entry point; its contract is in [pipeline/CONTRACT.md](pipeline/CONTRACT.md). The export includes actual summary coverage (current narratives, legacy summaries, pending, and unavailable), topic coverage, citation match rate, and coverage audit rather than conflating those states.

```sh
cd site
npm ci
npm run build
cd ..
python3 scripts/write-artifact-report.py site/dist
python3 scripts/validate-artifacts.py site/dist
```

The artifact report measures final site size after the build; this repository does not hard-code a size claim before that measurement. Validation enforces GitHub Pages’ 100 MB per-file and 1 GB site limits and rejects PDFs or remote generated assets. Pushes build and validate only committed static inputs; they never run collection or call research/LLM services. Pages deployment is a separate manual workflow-dispatch opt-in (`deploy=true`) and must remain off until this private repository and account support GitHub Pages.

## Sources and provenance

Official proceedings are the primary source: NeurIPS proceedings/virtual programme data and PMLR/ICML proceedings, supplemented by documented historical sources. Every generated summary is labelled beside its original-paper link and is bound to the official abstract hash used to author it. See [data sources](docs/data-sources.md), [architecture](docs/architecture.md), and [summary provenance](docs/summary-provenance.md).
