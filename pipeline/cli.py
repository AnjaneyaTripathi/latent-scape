from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .corpus import select, write_meta
from .enrich import enrich, merge_cached_enrichment
from .export import export_site_data
from .full_ingest import (
    audit_full_abstract_quality,
    extract_full_outbound_links,
    hydrate_full,
    repair_cached_page_chrome,
)
from .glossary import glossary
from .ingest import clean_sample_authors, ingest, repair_sample_outbound_links, replace_missing_sample_abstract
from .settings import load_settings
from .similarity import similar
from .summaries import import_summaries, refresh_summary_prompts, replace_summary_prompt, write_summary_batch
from .topics import cluster, import_topics


def embed(settings, corpus: str) -> dict:
    from .embedding import embed_texts
    papers, value = select(settings, corpus)
    vectors = embed_texts(
        [f"{paper['title']}\n{paper.get('abstract', '')}" for paper in papers],
        model_name=settings.embedding_model,
        cache_dir=settings.cache_dir / "models",
    )
    path = settings.derived_dir / f"embeddings-{corpus}.npy"
    np.save(path, vectors)
    write_meta(path, corpus, value)
    return {"papers": len(papers), "dimensions": int(vectors.shape[1])}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the cached Latentscape corpus")
    parser.add_argument("stage", choices=["ingest-sample", "ingest-full", "hydrate-full", "audit-full-abstracts", "repair-full-page-chrome", "extract-full-outbound-links", "repair-sample", "repair-links", "replace-missing", "enrich", "merge-enrichment", "embed", "cluster", "similarity", "glossary", "summary-batch", "replace-summary-prompt", "refresh-summary-prompts", "import-summaries", "import-topics", "export", "all-sample"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--old-slug")
    parser.add_argument("--new-slug")
    parser.add_argument("--new-sample", action="store_true", help="replace the reviewed sample selection")
    parser.add_argument("--corpus", choices=["sample", "full"], default="sample")
    parser.add_argument("--workers", type=int, default=6, help="bounded concurrent official-page requests")
    parser.add_argument("--checkpoint-every", type=int, default=100, help="full hydration checkpoint interval")
    parser.add_argument("--hydrate-limit", type=int, help="hydrate only this many pending full records")
    parser.add_argument("--skip-pmlr-bulk", action="store_true", help="fall back to ICML official landing pages")
    parser.add_argument("--skip-neurips-bulk", action="store_true", help="fall back to NeurIPS official landing pages")
    args = parser.parse_args()
    settings = load_settings(Path.cwd(), args.config)
    corpus = args.corpus
    if args.stage == "ingest-sample": result = ingest(settings, sample=True, freeze_sample=not args.new_sample)
    elif args.stage == "ingest-full": result = ingest(settings, sample=False, hydrate=False)
    elif args.stage == "hydrate-full":
        result = hydrate_full(
            settings, workers=args.workers, checkpoint_every=args.checkpoint_every, limit=args.hydrate_limit,
            use_pmlr_bulk=not args.skip_pmlr_bulk, use_neurips_bulk=not args.skip_neurips_bulk,
        )
    elif args.stage == "audit-full-abstracts": result = audit_full_abstract_quality(settings)
    elif args.stage == "repair-full-page-chrome": result = repair_cached_page_chrome(settings)
    elif args.stage == "extract-full-outbound-links": result = extract_full_outbound_links(settings)
    elif args.stage == "repair-sample": result = clean_sample_authors(settings)
    elif args.stage == "repair-links": result = repair_sample_outbound_links(settings)
    elif args.stage == "replace-missing": result = replace_missing_sample_abstract(settings)
    elif args.stage == "enrich": result = enrich(settings, corpus)
    elif args.stage == "merge-enrichment": result = merge_cached_enrichment(settings, corpus)
    elif args.stage == "embed": result = embed(settings, corpus)
    elif args.stage == "cluster": result = cluster(settings, corpus)
    elif args.stage == "similarity": result = similar(settings, corpus)
    elif args.stage == "glossary": result = glossary(settings, corpus)
    elif args.stage == "summary-batch": result = write_summary_batch(settings, args.limit, corpus)
    elif args.stage == "replace-summary-prompt": result = replace_summary_prompt(settings, args.old_slug or "", args.new_slug or "")
    elif args.stage == "refresh-summary-prompts": result = refresh_summary_prompts(settings)
    elif args.stage == "import-summaries":
        result = import_summaries(settings, args.summary_file, corpus)
        if result["errors"]:
            raise SystemExit(f"summary import rejected {len(result['errors'])} record(s); see summary-import-errors.json")
    elif args.stage == "import-topics": result = import_topics(settings, corpus)
    elif args.stage == "export": result = export_site_data(settings, corpus)
    else:
        result = {"enrich": merge_cached_enrichment(settings, "sample"), "embed": embed(settings, "sample"), "cluster": cluster(settings, "sample"), "similarity": similar(settings, "sample"), "glossary": glossary(settings, "sample"), "export": export_site_data(settings, "sample")}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
