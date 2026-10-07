from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

from haxlab.runtime.archive import ArchiveResult, archive_replay
from haxlab.runtime.state import RuntimeState


@dataclass(frozen=True)
class ScanSummary:
    discovered: int = 0
    unchanged: int = 0
    archived: int = 0
    duplicates: int = 0
    failed: int = 0
    disappeared: int = 0


def _native_finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def scan_once(
    incoming_root: Path,
    raw_root: Path,
    state: RuntimeState,
    *,
    minimum_file_age_seconds: float = 30.0,
    now: float | None = None,
) -> ScanSummary:
    if (
        not _native_finite_number(minimum_file_age_seconds)
        or float(minimum_file_age_seconds) < 0.0
    ):
        raise ValueError("minimum_file_age_seconds_must_be_finite_and_nonnegative")

    now = time.time() if now is None else now
    if not _native_finite_number(now):
        raise ValueError("now_must_be_finite")

    if incoming_root.is_symlink():
        state.event(
            "ingest_root_rejected",
            subject=str(incoming_root),
            detail="symlink_root",
        )
        raise ValueError("incoming_root_must_not_be_symlink")
    discovered = unchanged = archived = duplicates = failed = disappeared = 0

    candidates: list[tuple[Path, bool]] = []
    for root, dir_names, file_names in os.walk(
        incoming_root,
        topdown=True,
        followlinks=False,
    ):
        root_path = Path(root)

        retained_dirs: list[str] = []
        for name in dir_names:
            directory = root_path / name
            if directory.is_symlink():
                state.event(
                    "ingest_directory_rejected",
                    subject=str(directory),
                    detail="symlink_directory",
                )
                continue
            retained_dirs.append(name)
        dir_names[:] = retained_dirs

        for name in file_names:
            path = root_path / name
            if path.suffix.casefold() == ".hbr2":
                candidates.append((path, path.is_symlink()))

    paths = sorted(candidates, key=lambda item: str(item[0]).casefold())

    for path, was_symlink in paths:
        # is_file() follows symlinks, so retain the discovery-time observation
        # and re-check after sorting before any file metadata/content is trusted.
        if was_symlink or path.is_symlink():
            state.event(
                "replay_rejected",
                subject=str(path),
                detail="symlink_source",
            )
            continue
        if not path.is_file():
            continue

        discovered += 1
        try:
            stat = path.stat()
        except FileNotFoundError:
            # Uploaders may atomically rename/remove a file after rglob has
            # yielded it. That is not corrupt replay evidence and must not take
            # down the long-running ingest daemon.
            state.event(
                "replay_disappeared",
                subject=str(path),
                detail="disappeared_before_stat",
            )
            disappeared += 1
            continue

        # Ignore files which may still be uploading.
        if now - stat.st_mtime < minimum_file_age_seconds:
            continue

        source_key = str(path.resolve())
        known = state.get_source(source_key)
        if (
            known is not None
            and known.size_bytes == stat.st_size
            and known.mtime_ns == stat.st_mtime_ns
            and known.status in {"archived", "duplicate"}
        ):
            unchanged += 1
            continue

        try:
            result: ArchiveResult = archive_replay(path, raw_root, state)
            status = "duplicate" if result.duplicate else "archived"
            state.mark_seen(
                source_path=source_key,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=result.sha256,
                status=status,
            )
            state.event(
                "replay_duplicate" if result.duplicate else "replay_archived",
                subject=result.sha256,
                detail=source_key,
            )
            if result.duplicate:
                duplicates += 1
            else:
                archived += 1
        except Exception as exc:
            state.mark_seen(
                source_path=source_key,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=None,
                status="failed",
                error=str(exc),
            )
            state.event("replay_failed", subject=source_key, detail=str(exc))
            failed += 1

    return ScanSummary(
        discovered=discovered,
        unchanged=unchanged,
        archived=archived,
        duplicates=duplicates,
        failed=failed,
        disappeared=disappeared,
    )
