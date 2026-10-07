from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import struct
import time
import zlib
from pathlib import Path

from haxlab.replay.header import (
    HBR2_MAGIC,
    SUPPORTED_VERSION,
    ReplayFormatError,
    ReplayHeader,
)
from haxlab.runtime.state import RawReplayRecord, RuntimeState


_READ_CHUNK_BYTES = 1024 * 1024
_DECOMPRESS_CHUNK_BYTES = 1024 * 1024


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


def _batch_size_arg(value: str) -> int:
    if type(value) is not str or value != value.strip() or value == "":
        raise argparse.ArgumentTypeError(
            "batch size must be a positive integer"
        )

    try:
        batch_size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "batch size must be a positive integer"
        ) from exc

    if batch_size <= 0:
        raise argparse.ArgumentTypeError(
            "batch size must be a positive integer"
        )
    return batch_size


def _archive_open_failure(path: Path) -> ReplayFormatError:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return ReplayFormatError("archive_missing")
    except OSError:
        return ReplayFormatError("archive_secure_open_failed")
    if stat.S_ISLNK(current.st_mode):
        return ReplayFormatError("archive_symlink_not_allowed")
    return ReplayFormatError("archive_secure_open_failed")


def _verified_archive_bytes(replay: RawReplayRecord) -> bytes:
    path = Path(replay.archive_path)
    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise ReplayFormatError("archive_missing") from exc
    except OSError as exc:
        raise ReplayFormatError("archive_read_failed") from exc

    if stat.S_ISLNK(initial.st_mode):
        raise ReplayFormatError("archive_symlink_not_allowed")
    if not stat.S_ISREG(initial.st_mode):
        raise ReplayFormatError("archive_not_regular")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise ReplayFormatError("archive_secure_open_unsupported")

    flags = os.O_RDONLY | nofollow | nonblock
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _archive_open_failure(path) from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ReplayFormatError("archive_not_regular")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise ReplayFormatError("archive_identity_changed")

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            digest.update(chunk)

        after = os.fstat(fd)
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
            raise ReplayFormatError("archive_changed_during_read")

        try:
            final = path.lstat()
        except OSError as exc:
            raise ReplayFormatError("archive_path_changed_during_read") from exc
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise ReplayFormatError("archive_path_changed_during_read")
        final_identity = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if final_identity != after_identity:
            raise ReplayFormatError("archive_path_changed_during_read")

        if total != replay.size_bytes:
            raise ReplayFormatError(
                f"archive_size_mismatch:expected={replay.size_bytes}:actual={total}"
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != replay.sha256:
            raise ReplayFormatError(
                f"archive_sha256_mismatch:expected={replay.sha256}:actual={actual_sha256}"
            )

        return b"".join(chunks)
    except OSError as exc:
        raise ReplayFormatError("archive_read_failed") from exc
    finally:
        os.close(fd)


def _decompressed_payload_size(compressed: bytes) -> int:
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    total = 0
    remaining = compressed

    try:
        while True:
            output = decompressor.decompress(
                remaining,
                _DECOMPRESS_CHUNK_BYTES,
            )
            total += len(output)

            if decompressor.eof:
                return total

            remaining = decompressor.unconsumed_tail
            if remaining:
                continue

            remaining = b""
            if not output:
                raise ReplayFormatError("deflate_error:incomplete_stream")
    except zlib.error as exc:
        raise ReplayFormatError(f"deflate_error:{exc}") from exc


def _probe_verified_replay(data: bytes) -> tuple[ReplayHeader, int]:
    if len(data) < 12:
        raise ReplayFormatError("truncated_header")

    magic, version, total_frames = struct.unpack(">4sII", data[:12])
    if magic != HBR2_MAGIC:
        raise ReplayFormatError("invalid_magic")
    if version != SUPPORTED_VERSION:
        raise ReplayFormatError(f"unsupported_version:{version}")

    decompressed_bytes = _decompressed_payload_size(data[12:])
    return ReplayHeader(
        version=version,
        total_frames=total_frames,
    ), decompressed_bytes


def process_batch(state: RuntimeState, *, batch_size: int = 50) -> dict[str, int]:
    pending = state.list_unprocessed_replays(limit=batch_size)
    ok = failed = 0

    for replay in pending:
        try:
            archive_bytes = _verified_archive_bytes(replay)
            header, decompressed_bytes = _probe_verified_replay(archive_bytes)

            state.mark_replay_processing(
                sha256=replay.sha256,
                status="ok",
                format_version=header.version,
                total_frames=header.total_frames,
                duration_seconds=header.duration_seconds,
                decompressed_bytes=decompressed_bytes,
                parser_stage="probe",
            )
            state.event(
                "replay_probe_ok",
                subject=replay.sha256,
                detail=(
                    f"frames={header.total_frames};"
                    f"duration={header.duration_seconds:.3f};"
                    f"decompressed_bytes={decompressed_bytes}"
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haxlab-worker")
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--interval", type=_poll_interval_arg, default=5.0)
    parser.add_argument("--batch-size", type=_batch_size_arg, default=50)
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

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
