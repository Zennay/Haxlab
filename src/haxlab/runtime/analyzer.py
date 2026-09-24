from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RawReplayRecord, RuntimeState


ANALYZER_VERSION = CURRENT_ANALYZER_VERSION


def _analyze_one(
    replay: RawReplayRecord,
    *,
    decoder_script: Path,
    derived_root: Path,
    sample_every_ticks: int,
    timeout_seconds: int,
) -> tuple[RawReplayRecord, dict[str, Any] | None, str | None, Path | None]:
    output_dir = derived_root / ANALYZER_VERSION / replay.sha256[:2] / replay.sha256[2:4]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{replay.sha256}.json"

    if output_path.exists():
        try:
            return replay, json.loads(output_path.read_text(encoding="utf-8")), None, output_path
        except (OSError, json.JSONDecodeError):
            pass

    command = [
        "node",
        str(decoder_script),
        replay.archive_path,
        str(max(1, sample_every_ticks)),
    ]

    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return replay, None, f"decoder_timeout:{timeout_seconds}s", None
    except OSError as exc:
        return replay, None, f"decoder_exec_error:{exc}", None

    if completed.returncode != 0:
        stderr = completed.stderr.strip()[-4000:]
        return replay, None, f"decoder_exit_{completed.returncode}:{stderr}", None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        stdout_tail = completed.stdout[-1000:]
        stderr_tail = completed.stderr[-1000:]
        return (
            replay,
            None,
            f"decoder_json_error:{exc};stdout={stdout_tail!r};stderr={stderr_tail!r}",
            None,
        )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{replay.sha256}.",
        suffix=".tmp",
        dir=output_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise

    return replay, payload, None, output_path


def analyze_batch(
    state: RuntimeState,
    *,
    decoder_script: Path,
    derived_root: Path,
    batch_size: int = 16,
    workers: int = 4,
    sample_every_ticks: int = 6,
    timeout_seconds: int = 120,
) -> dict[str, int]:
    pending = state.list_unanalyzed_replays(\n        limit=max(1, batch_size),\n        analyzer_version=ANALYZER_VERSION,\n    )
    ok = failed = 0

    if not pending:
        return {"selected": 0, "ok": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(
                _analyze_one,
                replay,
                decoder_script=decoder_script,
                derived_root=derived_root,
                sample_every_ticks=sample_every_ticks,
                timeout_seconds=timeout_seconds,
            )
            for replay in pending
        ]

        for future in as_completed(futures):
            replay, payload, error, output_path = future.result()
            if error is not None or payload is None:
                state.mark_replay_analysis(
                    sha256=replay.sha256,
                    status="failed",
                    analyzer_version=ANALYZER_VERSION,
                    error=error or "unknown_analysis_error",
                )
                state.event(
                    "replay_analysis_failed",
                    subject=replay.sha256,
                    detail=error or "unknown_analysis_error",
                )
                failed += 1
                continue

            simulation = payload.get("simulation") or {}
            total_frames = int(payload.get("totalFrames") or 0)
            frames_advanced = int(simulation.get("framesAdvanced") or 0)
            sampled_states = int(simulation.get("sampledStateCount") or 0)

            if total_frames > 0 and frames_advanced < max(0, total_frames - 1):
                error = (
                    f"incomplete_state_reconstruction:"
                    f"{frames_advanced}/{total_frames}"
                )
                state.mark_replay_analysis(
                    sha256=replay.sha256,
                    status="failed",
                    analyzer_version=ANALYZER_VERSION,
                    error=error,
                )
                state.event(
                    "replay_analysis_failed",
                    subject=replay.sha256,
                    detail=error,
                )
                failed += 1
                continue

            state.mark_replay_analysis(
                sha256=replay.sha256,
                status="ok",
                analyzer_version=ANALYZER_VERSION,
                output_path=str(output_path) if output_path else None,
                sampled_state_count=sampled_states,
                player_count=len(payload.get("players") or []),
                raw_event_count=int(payload.get("rawEventCount") or 0),
                tick_count=frames_advanced,
            )
            state.event(
                "replay_analysis_ok",
                subject=replay.sha256,
                detail=(
                    f"players={len(payload.get('players') or [])};"
                    f"events={int(payload.get('rawEventCount') or 0)};"
                    f"samples={sampled_states};"
                    f"frames={frames_advanced}/{total_frames}"
                ),
            )
            ok += 1

    return {"selected": len(pending), "ok": ok, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-analyzer")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument(
        "--derived-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived"),
    )
    parser.add_argument(
        "--decoder-script",
        type=Path,
        default=Path("/opt/haxlab/tools/decode_replay.js"),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--sample-every-ticks", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    args.derived_root.mkdir(parents=True, exist_ok=True)

    with RuntimeState(args.state_db) as state:
        while True:
            result = analyze_batch(
                state,
                decoder_script=args.decoder_script,
                derived_root=args.derived_root,
                batch_size=max(1, args.batch_size),
                workers=max(1, args.workers),
                sample_every_ticks=max(1, args.sample_every_ticks),
                timeout_seconds=max(10, args.timeout_seconds),
            )
            print(json.dumps(result, sort_keys=True), flush=True)

            if args.once:
                return 0
            if result["selected"] == 0:
                time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
