"""Copy the browser-vendored MiniLM model to a pipeline model cache without network I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("site/public/models/Xenova/all-MiniLM-L6-v2")
    )
    parser.add_argument("--cache", type=Path, default=Path("pipeline/.cache/models"))
    args = parser.parse_args()
    source = args.source.resolve()
    audit = json.loads((source / "ASSET-AUDIT.json").read_text())
    for relative, expected in audit["files"].items():
        actual = sha256(source / relative)
        if actual != expected:
            raise SystemExit(f"checksum mismatch for {relative}: expected {expected}, got {actual}")
    target = args.cache.resolve() / "Xenova" / "all-MiniLM-L6-v2"
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"Verified and staged {audit['model']}@{audit['revision']} at {target}")


if __name__ == "__main__":
    main()
