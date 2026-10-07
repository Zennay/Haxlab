from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from haxlab.runtime.state import RuntimeState


def _require_nonnegative_int(snapshot: dict[str, object], key: str) -> int:
    value = snapshot.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_status_snapshot:{key}")
    return value


def _require_nonnegative_finite(
    snapshot: dict[str, object],
    key: str,
) -> float:
    value = snapshot.get(key)
    if type(value) not in (int, float):
        raise ValueError(f"invalid_status_snapshot:{key}")

    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"invalid_status_snapshot:{key}") from exc

    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"invalid_status_snapshot:{key}")
    return numeric


def _validate_status_snapshot(snapshot: dict[str, object]) -> dict[str, float | int]:
    raw_unique = _require_nonnegative_int(snapshot, "raw_unique_replays")
    processing_ok = _require_nonnegative_int(snapshot, "processing_ok")
    processing_failed = _require_nonnegative_int(snapshot, "processing_failed")
    processing_pending = _require_nonnegative_int(snapshot, "processing_pending")
    analysis_ok = _require_nonnegative_int(snapshot, "analysis_ok")
    analysis_failed = _require_nonnegative_int(snapshot, "analysis_failed")
    analysis_pending = _require_nonnegative_int(snapshot, "analysis_pending")

    if processing_ok + processing_failed + processing_pending > raw_unique:
        raise ValueError("invalid_status_snapshot:processing_counts_exceed_raw")
    if analysis_ok + analysis_failed + analysis_pending > processing_ok:
        raise ValueError(
            "invalid_status_snapshot:analysis_counts_exceed_processing_ok"
        )

    return {
        "raw_unique_replays": raw_unique,
        "processing_ok": processing_ok,
        "processing_failed": processing_failed,
        "processing_pending": processing_pending,
        "analysis_ok": analysis_ok,
        "analysis_failed": analysis_failed,
        "analysis_pending": analysis_pending,
        "duration_seconds_probed": _require_nonnegative_finite(
            snapshot,
            "duration_seconds_probed",
        ),
        "probe_rate_per_minute_5m": _require_nonnegative_finite(
            snapshot,
            "probe_rate_per_minute_5m",
        ),
        "analysis_rate_per_minute_5m": _require_nonnegative_finite(
            snapshot,
            "analysis_rate_per_minute_5m",
        ),
    }


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

    metrics = _validate_status_snapshot(snapshot)

    snapshot["duration_hours_probed"] = round(
        metrics["duration_seconds_probed"] / 3600.0,
        2,
    )

    probe_rate = float(metrics["probe_rate_per_minute_5m"])
    probe_pending = int(metrics["processing_pending"])
    snapshot["estimated_probe_minutes_remaining"] = (
        round(probe_pending / probe_rate, 1)
        if probe_pending > 0 and probe_rate > 0
        else None
    )

    raw_unique = int(metrics["raw_unique_replays"])
    processing_ok = int(metrics["processing_ok"])
    analysis_ok = int(metrics["analysis_ok"])

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
        and int(metrics["analysis_failed"]) == 0
    )

    analysis_rate = float(metrics["analysis_rate_per_minute_5m"])
    analysis_pending = int(metrics["analysis_pending"])
    snapshot["estimated_analysis_minutes_remaining"] = (
        round(analysis_pending / analysis_rate, 1)
        if analysis_pending > 0 and analysis_rate > 0
        else None
    )

    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
