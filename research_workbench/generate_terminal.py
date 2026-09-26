"""Rebuild the public annual-financial cube from checked-in SEC archives."""
from __future__ import annotations

import argparse
from pathlib import Path

from .terminal_data import ARCHIVE_DIR, DEFAULT_CUBE, ROOT, archive_cached_sources, build_cube, canonical, load_sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_CUBE)
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    parser.add_argument("--freeze-cache", type=Path, help="Explicitly archive existing downloaded cache; makes no network/model calls.")
    args = parser.parse_args()
    if args.freeze_cache:
        archive_cached_sources(args.freeze_cache, args.archive_dir)
    payloads, manifest = load_sources(args.archive_dir)
    cube = build_cube(payloads, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(cube) + b"\n")
    print({"output": str(args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output), **cube["statistics"], "bytes": args.output.stat().st_size})


if __name__ == "__main__":
    main()
