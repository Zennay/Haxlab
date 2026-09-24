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
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".hbr2"),
        key=lambda path: str(path).casefold(),
    )

    files: list[ReplayFile] = []
    by_hash: dict[str, list[ReplayFile]] = {}

    for path in paths:
        digest = sha256_file(path)
        replay = ReplayFile.from_path(path, digest)
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
