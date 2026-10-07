from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

from haxlab.runtime.scanner import scan_once
from haxlab.runtime.state import RuntimeState


def _finite_seconds_arg(
    value: str,
    *,
    name: str,
    minimum: float,
) -> float:
    message = f"{name} must be a finite number greater than or equal to {minimum:g}"
    if type(value) is not str or value == "" or value != value.strip():
        raise argparse.ArgumentTypeError(message)

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(message) from exc

    if not math.isfinite(parsed) or parsed < minimum:
        raise argparse.ArgumentTypeError(message)
    return parsed


def _poll_interval_arg(value: str) -> float:
    return _finite_seconds_arg(value, name="interval", minimum=1.0)


def _minimum_file_age_arg(value: str) -> float:
    return _finite_seconds_arg(value, name="minimum file age", minimum=0.0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haxlab-daemon")
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--interval", type=_poll_interval_arg, default=60.0)
    parser.add_argument(
        "--minimum-file-age",
        type=_minimum_file_age_arg,
        default=30.0,
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

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
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
