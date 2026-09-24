from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from haxlab.runtime.scanner import scan_once
from haxlab.runtime.state import RuntimeState


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-daemon")
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--minimum-file-age", type=float, default=30.0)
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
