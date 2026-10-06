from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from haxlab.hashing import sha256_file
from haxlab.models import ReplayFile


@dataclass(frozen=True)
class ReplayInventory:
    all_files: tuple[ReplayFile, ...]
    unique_files: tuple[ReplayFile, ...]
    duplicate_paths_by_hash: dict[str, tuple[str, ...]]


def discover_replays(root: Path) -> ReplayInventory:
    """Discover every .hbr2 file below root and deduplicate by content hash."""
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".hbr2"
        ),
        key=lambda path: str(path).casefold(),
    )

    files: list[ReplayFile] = []
    by_hash: dict[str, list[ReplayFile]] = {}

    for path in paths:
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
        if path.is_symlink() or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError(f"source_changed_during_hash:{path}")

        replay = ReplayFile(
            path=str(path),
            file_name=path.name,
            size_bytes=after.st_size,
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
