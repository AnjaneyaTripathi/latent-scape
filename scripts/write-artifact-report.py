#!/usr/bin/env python3
"""Write the size and corpus audit shipped with a built Pages artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def size_of(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="site/dist")
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    data = root / "data"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((data / "coverage.json").read_text(encoding="utf-8"))
    topics = json.loads((data / "topics.json").read_text(encoding="utf-8"))
    corpus = json.loads((data / "corpus-report.json").read_text(encoding="utf-8"))
    model = json.loads((root / "models/Xenova/all-MiniLM-L6-v2/ASSET-AUDIT.json").read_text(encoding="utf-8"))
    destination = data / "report.json"
    report = {
        "snapshot": manifest.get("snapshot"),
        "papers": manifest.get("totalPapers", 0),
        "topics": manifest.get("totalTopics", len(topics)),
        "summaries": 0,
        "coverage": coverage,
        "papersByVenueYear": corpus.get("papersByVenueYear", {}),
        "citationMatchRate": corpus.get("citationMatchRate"),
        "model": {key: model.get(key) for key in ("model", "revision", "runtime", "source")},
        "siteBytes": 0,
    }
    for shard in (manifest.get("shards", {}).get("papers", {}) or {}).values():
        papers = json.loads((data / shard).read_text(encoding="utf-8"))
        report["summaries"] += sum(paper.get("summary") is not None for paper in papers)
    # The report contributes to the total, so converge after its formatted size settles.
    for _ in range(3):
        report["siteBytes"] = size_of(root) - (destination.stat().st_size if destination.exists() else 0)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        destination.write_text(encoded, encoding="utf-8")
    report["siteBytes"] = size_of(root)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(destination), "siteBytes": size_of(root)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
