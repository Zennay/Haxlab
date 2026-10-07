from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from haxlab.models import ReplayFile


_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ReplayInventory:
    all_files: tuple[ReplayFile, ...]
    unique_files: tuple[ReplayFile, ...]
    duplicate_paths_by_hash: dict[str, tuple[str, ...]]


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _snapshot_replay(path: Path, *, resolved_root: Path) -> tuple[str, int]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"replay_changed_during_discovery:{path}") from exc

    if stat.S_ISLNK(initial.st_mode):
        raise ValueError(f"symlinked_replay_not_allowed:{path}")
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"replay_not_regular:{path}")

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"replay_changed_during_discovery:{path}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"replay_outside_discovery_root:{path}")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise ValueError("secure_replay_open_unsupported")

    flags = os.O_RDONLY | nofollow | nonblock
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"replay_secure_open_failed:{path}") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"replay_not_regular:{path}")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise ValueError(f"replay_identity_changed:{path}")

        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)

        after = os.fstat(fd)
        if total != after.st_size or _stat_signature(before) != _stat_signature(after):
            raise ValueError(f"replay_changed_during_discovery:{path}")

        try:
            final = path.lstat()
            final_resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"replay_changed_during_discovery:{path}") from exc
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise ValueError(f"replay_changed_during_discovery:{path}")
        if _stat_signature(final) != _stat_signature(after):
            raise ValueError(f"replay_changed_during_discovery:{path}")
        if not final_resolved.is_relative_to(resolved_root):
            raise ValueError(f"replay_outside_discovery_root:{path}")

        return digest.hexdigest(), total
    finally:
        os.close(fd)


def _candidate_paths(root: Path) -> list[Path]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError(f"discovery_root_unavailable:{root}") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError(f"symlinked_discovery_root_not_allowed:{root}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"discovery_root_not_directory:{root}")

    def walk_error(exc: OSError) -> None:
        raise ValueError(f"discovery_walk_failed:{root}:{exc}") from exc

    candidates: list[Path] = []
    for current, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        safe_dirnames: list[str] = []
        for name in sorted(dirnames, key=str.casefold):
            child = current_path / name
            try:
                child_stat = child.lstat()
            except OSError as exc:
                raise ValueError(
                    f"discovery_directory_unreadable:{child}"
                ) from exc
            if stat.S_ISLNK(child_stat.st_mode):
                continue
            if not stat.S_ISDIR(child_stat.st_mode):
                raise ValueError(f"discovery_directory_not_directory:{child}")
            safe_dirnames.append(name)
        dirnames[:] = safe_dirnames

        for name in sorted(filenames, key=str.casefold):
            path = current_path / name
            if path.suffix.casefold() != ".hbr2":
                continue
            candidates.append(path)

    return sorted(candidates, key=lambda path: str(path).casefold())


def discover_replays(root: Path) -> ReplayInventory:
    """Discover immutable in-tree .hbr2 files and deduplicate by content hash."""

    resolved_root = root.resolve(strict=True)
    paths = _candidate_paths(root)

    files: list[ReplayFile] = []
    by_hash: dict[str, list[ReplayFile]] = {}

    for path in paths:
        digest, size_bytes = _snapshot_replay(path, resolved_root=resolved_root)
        replay = ReplayFile(
            path=str(path),
            file_name=path.name,
            size_bytes=size_bytes,
            sha256=digest,
        )
        files.append(replay)
        by_hash.setdefault(digest, []).append(replay)

    unique = tuple(group[0] for _, group in sorted(by_hash.items()))
    duplicates = {
        digest: tuple(item.path for item in group[1:])
        for digest, group in by_hash.items()
        if len(group) > 1
    }

    return ReplayInventory(
        all_files=tuple(files),
        unique_files=unique,
        duplicate_paths_by_hash=duplicates,
    )
