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

    probe_rate = float(snapshot.get("probe_rate_per_minute_5m", 0.0))
    probe_pending = int(snapshot.get("processing_pending", 0))
    snapshot["estimated_probe_minutes_remaining"] = (
        round(probe_pending / probe_rate, 1)
        if probe_pending > 0 and probe_rate > 0
        else None
    )

    raw_unique = int(snapshot.get("raw_unique_replays", 0))
    processing_ok = int(snapshot.get("processing_ok", 0))
    analysis_ok = int(snapshot.get("analysis_ok", 0))

    snapshot["processing_progress_percent"] = (
        round(100.0 * processing_ok / raw_unique, 2)
        if raw_unique > 0
        else 0.0
    )
    snapshot["analysis_progress_percent"] = (
        round(100.0 * analysis_ok / raw_unique, 2)
        if raw_unique > 0
        else 0.0
    )
    snapshot["analysis_complete"] = (
        raw_unique > 0
        and analysis_ok == raw_unique
        and int(snapshot.get("analysis_failed", 0)) == 0
    )

    analysis_rate = float(snapshot.get("analysis_rate_per_minute_5m", 0.0))
    analysis_pending = int(snapshot.get("analysis_pending", 0))
    snapshot["estimated_analysis_minutes_remaining"] = (
        round(analysis_pending / analysis_rate, 1)
        if analysis_pending > 0 and analysis_rate > 0
        else None
    )

    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
