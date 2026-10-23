# Full-corpus build

The full build is a one-time, offline pipeline. GitHub Pages only receives the
exported static files; it never runs these commands or calls research or LLM
services.

```python
from pathlib import Path
from pipeline.full_build import prepare_full, finalize_full, full_status
from pipeline.settings import load_settings

settings = load_settings(Path.cwd())
prepare_full(settings)  # cache-aware embeddings, 256 fine / 32 broad clusters
# Run the Astra narrative worker separately, then import its validated shards.
finalize_full(settings)
```

The executable workflow is equivalent:

```sh
python -m pipeline.full_build prepare
python -m pipeline.full_build status
python -m pipeline.full_build generate
python -m pipeline.full_build finalize --allow-incomplete
```

For the one-time unattended pass, use `run` instead. It waits for the
resumable Astra authoring worker, then imports summaries, applies topic guides,
exports static data, builds the site, and validates the artifact:

```sh
python -m pipeline.full_build run --allow-incomplete
```

`generate` freezes `full-paper-summary-input.json`, runs up to four Astra
workers, and records the PID, command, status, and log under the derived/status
and narrative working directories. It only asks Astra about eligible abstracts;
missing or insufficient source text is counted as unavailable, never invented.
It writes new shards to `data/generated/narratives/full/` and checks existing
reviewed shards in `data/generated/narratives/`, then freezes only those whose
abstract hashes still match into `pipeline/data/derived/full-summary-exclusions.json`.
The authoring run uses that immutable list rather than its active output tree,
so a restart retains the original batch boundaries.
`finalize` normally requires every paper and topic guide. `--allow-incomplete`
permits a clearly incomplete private preview but never changes `readyForRelease`.

`prepare_full` is safe to resume because embedding checkpoints are keyed by
source text hash. It writes the full topic-curation input but does not invent
editorial prose. `finalize_full` imports only schema- and hash-valid summaries,
refuses to export while any paper remains pending, then computes similarity,
glossary, and static data. `full_status` reports counts and is the release gate.
