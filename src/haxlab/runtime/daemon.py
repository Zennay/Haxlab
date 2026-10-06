from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

from haxlab.runtime.scanner import scan_once
from haxlab.runtime.state import RuntimeState


def _finite_non_negative_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-daemon")
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument(
        "--interval",
        type=_finite_non_negative_seconds,
        default=60.0,
    )
    parser.add_argument(
        "--minimum-file-age",
        type=_finite_non_negative_seconds,
        default=30.0,
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    args.incoming.mkdir(parents=True, exist_ok=True)
    args.raw.mkdir(parents=True, exist_ok=True)

    with RuntimeState(args.state_db) as state:
        while True:
            summary = scan_once(
                args.incoming,
                args.raw,
                state,
                minimum_file_age_seconds=args.minimum_file_age,
            )
            print(json.dumps(asdict(summary), sort_keys=True), flush=True)

            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
