from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from haxlab.runtime.finalize import finalize_analysis_if_ready
from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RawReplayRecord, RuntimeState


ANALYZER_VERSION = CURRENT_ANALYZER_VERSION
_READ_CHUNK_BYTES = 1024 * 1024


class AnalyzerArchiveError(ValueError):
    pass


def _archive_open_failure(path: Path) -> AnalyzerArchiveError:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return AnalyzerArchiveError("archive_missing")
    except OSError:
        return AnalyzerArchiveError("archive_secure_open_failed")
    if stat.S_ISLNK(current.st_mode):
        return AnalyzerArchiveError("archive_symlink_not_allowed")
    return AnalyzerArchiveError("archive_secure_open_failed")


def _prepare_verified_decode_input(
    replay: RawReplayRecord,
    *,
    output_dir: Path,
) -> tuple[int, str]:
    source = Path(replay.archive_path)
    try:
        initial = source.lstat()
    except FileNotFoundError as exc:
        raise AnalyzerArchiveError("archive_missing") from exc
    except OSError as exc:
        raise AnalyzerArchiveError("archive_read_failed") from exc

    if stat.S_ISLNK(initial.st_mode):
        raise AnalyzerArchiveError("archive_symlink_not_allowed")
    if not stat.S_ISREG(initial.st_mode):
        raise AnalyzerArchiveError("archive_not_regular")
    if initial.st_size != replay.size_bytes:
        raise AnalyzerArchiveError(
            f"archive_size_mismatch:expected={replay.size_bytes}:actual={initial.st_size}"
        )

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise AnalyzerArchiveError("archive_secure_open_unsupported")
    proc_fd_root = Path("/proc/self/fd")
    if os.name != "posix" or not proc_fd_root.is_dir():
        raise AnalyzerArchiveError("decode_fd_transport_unsupported")

    flags = os.O_RDONLY | nofollow | nonblock
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise _archive_open_failure(source) from exc

    temporary_name: str | None = None
    temporary_fd = -1
    keep_snapshot = False
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AnalyzerArchiveError("archive_not_regular")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise AnalyzerArchiveError("archive_identity_changed")

        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=f".{replay.sha256}.decode.",
                suffix=".hbr2",
                dir=output_dir,
            )
        except OSError as exc:
            raise AnalyzerArchiveError("decode_snapshot_create_failed") from exc

        digest = hashlib.sha256()
        total = 0
        with os.fdopen(os.dup(temporary_fd), "wb") as handle:
            while True:
                chunk = os.read(source_fd, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        after = os.fstat(source_fd)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != before_identity or total != after.st_size:
            raise AnalyzerArchiveError("archive_changed_during_read")

        try:
            final = source.lstat()
        except OSError as exc:
            raise AnalyzerArchiveError("archive_path_changed_during_read") from exc
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise AnalyzerArchiveError("archive_path_changed_during_read")
        final_identity = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if final_identity != after_identity:
            raise AnalyzerArchiveError("archive_path_changed_during_read")

        if total != replay.size_bytes:
            raise AnalyzerArchiveError(
                f"archive_size_mismatch:expected={replay.size_bytes}:actual={total}"
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != replay.sha256:
            raise AnalyzerArchiveError(
                f"archive_sha256_mismatch:expected={replay.sha256}:actual={actual_sha256}"
            )

        os.lseek(temporary_fd, 0, os.SEEK_SET)
        os.unlink(temporary_name)
        temporary_name = None
        keep_snapshot = True
        return temporary_fd, f"/proc/self/fd/{temporary_fd}"
    except OSError as exc:
        raise AnalyzerArchiveError("archive_read_failed") from exc
    finally:
        os.close(source_fd)
        if temporary_fd >= 0 and not keep_snapshot:
            os.close(temporary_fd)
        if temporary_name is not None and not keep_snapshot:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _positive_native_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a native positive integer")
    return value


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

    # Selection means there is no committed successful analysis row for this
    # analyzer version. A pre-existing derived file can only be orphan/stale
    # evidence from an interrupted or tampered run, so never promote it by
    # reuse. Re-run the decoder and replace it atomically below.
    #
    # The probe-worker's earlier archive check is not sufficient here: the raw
    # object can change between probe and analysis. Snapshot the exact verified
    # ledger bytes immediately before decode and give Node only that snapshot.
    try:
        decode_fd, decode_input = _prepare_verified_decode_input(
            replay,
            output_dir=output_dir,
        )
    except AnalyzerArchiveError as exc:
        return replay, None, f"archive_integrity_error:{exc}", None

    command = [
        "node",
        str(decoder_script),
        decode_input,
        str(max(1, sample_every_ticks)),
    ]

    completed: subprocess.CompletedProcess[str] | None = None
    run_error: str | None = None
    try:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                check=False,
                pass_fds=(decode_fd,),
            )
        except subprocess.TimeoutExpired:
            run_error = f"decoder_timeout:{timeout_seconds}s"
        except OSError as exc:
            run_error = f"decoder_exec_error:{exc}"
    finally:
        os.close(decode_fd)

    if run_error is not None:
        return replay, None, run_error, None
    assert completed is not None

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
    validated_workers = _positive_native_int(workers, name="workers")
    pending = state.list_unanalyzed_replays(
        limit=batch_size,
        analyzer_version=ANALYZER_VERSION,
    )
    ok = failed = 0

    if not pending:
        return {"selected": 0, "ok": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=validated_workers) as executor:
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
                batch_size=args.batch_size,
                workers=args.workers,
                sample_every_ticks=max(1, args.sample_every_ticks),
                timeout_seconds=max(10, args.timeout_seconds),
            )
            print(json.dumps(result, sort_keys=True), flush=True)

            if result["selected"] == 0:
                finalization = finalize_analysis_if_ready(
                    state,
                    derived_root=args.derived_root,
                )
                if finalization.get("status") == "finalized":
                    print(
                        json.dumps(
                            {"analysis_finalization": finalization},
                            sort_keys=True,
                        ),
                        flush=True,
                    )

            if args.once:
                return 0
            if result["selected"] == 0:
                time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
