# Astra narrative runner

`python -m pipeline.astra_runner` creates resumable, locally authenticated Codex
jobs for abstract-grounded paper explanations. It uses `gpt-6-astra` with an
ephemeral, read-only Codex sandbox; it does not require or read an API key.

The input is a JSON list of records with `paperSlug` (or `slug`), `title`, and
`abstract`. A `sourceAbstractSha256` field is optional on input and is verified
when supplied. The runner writes only validated JSON summary maps at the output
directory's top level; manifests, response diagnostics, partial checkpoints,
and schemas remain in `output-dir/working/` so import discovery never mistakes
them for summaries.

For the full proceedings run, use the orchestrated command in
[`full-build.md`](full-build.md):

```sh
python -m pipeline.full_build generate
```

It freezes the input and the matching reviewed-summary exclusion snapshot before
starting the run. Use this module directly only for a separate sample or review
directory:

```sh
python -m pipeline.astra_runner --input /path/to/records.json --output-dir /path/to/new-run --dry-run
```

Remove `--dry-run` only after reviewing the generated manifest. The runner
defaults to batches of 25 and two concurrent batches (at most four). Each batch requires
Codex to return a `slug → summary` JSON map with a TLDR, three to five
plain-language paragraphs, the exact abstract hash, difficulty, provenance, and
background topic slugs. It rejects output that is not abstract-grounded, has an
incorrect hash, includes unsupported topic slugs, or falls outside 120–240 words
for the explanatory paragraphs. New Astra prompts target 140–240 words; the
lower validation floor preserves reviewed manual shards that already meet the
three-to-five paragraph contract.

Use `--limit 1` for a single-record acceptance check and `--exclude-file` for a
JSON/list or newline-separated set of slugs. The existing-summary directory must
be a separate, immutable directory of reviewed shards; it cannot be the active
output directory or one of its parents. Re-run the exact same command to resume:
the runner restores its frozen eligible-record order and skips already validated
batches without calling Astra again. Records with missing or placeholder
abstracts are listed in `unavailable.json`; the runner never generates
title-only summaries.

`working/manifest.json` checkpoints after every finished batch, records
estimated input/output tokens, and retains every token-usage event Codex reports
in append-only per-batch attempt journals. It binds the output directory to the
ordered input snapshot, model, validation revision, and batch size; use a new
output directory if any of those changes. Account dollar cost is intentionally
recorded as unknown, not zero.
