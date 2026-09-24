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

    snapshot["duration_hours_probed"] = round(
        snapshot["duration_seconds_probed"] / 3600.0,
        2,
    )

    rate = float(snapshot.get("probe_rate_per_minute_5m", 0.0))
    pending = int(snapshot.get("processing_pending", 0))
    snapshot["estimated_probe_minutes_remaining"] = (
        round(pending / rate, 1) if pending > 0 and rate > 0 else None
    )

    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
