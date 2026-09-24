from __future__ import annotations

import argparse
import json
from pathlib import Path

from haxlab.runtime.state import RuntimeState


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-status")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    args = parser.parse_args()

    with RuntimeState(args.state_db) as state:
        snapshot = state.status_snapshot()

    duration_hours = snapshot["duration_seconds_probed"] / 3600.0
    snapshot["duration_hours_probed"] = round(duration_hours, 2)

    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
