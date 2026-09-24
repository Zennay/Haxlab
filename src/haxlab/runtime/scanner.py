from __future__ import annotations

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


def scan_once(
    incoming_root: Path,
    raw_root: Path,
    state: RuntimeState,
    *,
    minimum_file_age_seconds: float = 30.0,
    now: float | None = None,
) -> ScanSummary:
    now = time.time() if now is None else now
    discovered = unchanged = archived = duplicates = failed = 0

    paths = sorted(
        (
            path
            for path in incoming_root.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".hbr2"
        ),
        key=lambda path: str(path).casefold(),
    )

    for path in paths:
        discovered += 1
        stat = path.stat()

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
    )
