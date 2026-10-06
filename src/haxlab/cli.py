from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from haxlab.ingestion.pipeline import run_import


def _existing_directory(value: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"directory does not exist: {value}")
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"not a directory: {value}")
    return path


def _bounded_confidence(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a finite number in [0, 1]"
        ) from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("must be a finite number in [0, 1]")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haxlab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest",
        help="Build a deterministic M0 dataset inventory from a Discord export.",
    )
    ingest.add_argument("export_root", type=_existing_directory)
    ingest.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Derived output directory. Must be outside export_root.",
    )
    ingest.add_argument(
        "--minimum-match-confidence",
        type=_bounded_confidence,
        default=0.65,
    )

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.command == "ingest":
        manifest = run_import(
            args.export_root,
            args.output,
            minimum_match_confidence=args.minimum_match_confidence,
        )
        print(json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
