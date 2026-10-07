from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from haxlab.replay.header import (
    ReplayFormatError,
    decompress_replay_payload,
    read_replay_header,
)
from haxlab.runtime.state import RuntimeState


def _poll_interval_arg(value: str) -> float:
    if type(value) is not str or value != value.strip() or value == "":
        raise argparse.ArgumentTypeError(
            "interval must be a finite number greater than or equal to 1 second"
        )

    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "interval must be a finite number greater than or equal to 1 second"
        ) from exc

    if not math.isfinite(interval) or interval < 1.0:
        raise argparse.ArgumentTypeError(
            "interval must be a finite number greater than or equal to 1 second"
        )
    return interval


def process_batch(state: RuntimeState, *, batch_size: int = 50) -> dict[str, int]:
    pending = state.list_unprocessed_replays(limit=batch_size)
    ok = failed = 0

    for replay in pending:
        path = Path(replay.archive_path)
        try:
            header = read_replay_header(path)
            payload = decompress_replay_payload(path)

            state.mark_replay_processing(
                sha256=replay.sha256,
                status="ok",
                format_version=header.version,
                total_frames=header.total_frames,
                duration_seconds=header.duration_seconds,
                decompressed_bytes=len(payload),
                parser_stage="probe",
            )
            state.event(
                "replay_probe_ok",
                subject=replay.sha256,
                detail=(
                    f"frames={header.total_frames};"
                    f"duration={header.duration_seconds:.3f};"
                    f"decompressed_bytes={len(payload)}"
                ),
            )
            ok += 1
        except (OSError, ReplayFormatError, ValueError) as exc:
            state.mark_replay_processing(
                sha256=replay.sha256,
                status="failed",
                parser_stage="probe",
                error=str(exc),
            )
            state.event(
                "replay_probe_failed",
                subject=replay.sha256,
                detail=str(exc),
            )
            failed += 1

    return {
        "selected": len(pending),
        "ok": ok,
        "failed": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-worker")
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--interval", type=_poll_interval_arg, default=5.0)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    with RuntimeState(args.state_db) as state:
        while True:
            result = process_batch(state, batch_size=args.batch_size)
            print(json.dumps(result, sort_keys=True), flush=True)

            if args.once:
                return 0

            if result["selected"] == 0:
                time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
