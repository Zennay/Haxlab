"""Read-only deterministic byte and count comparison of two M0 import publications.

This is a diagnostic, not a replacement for receipt or provenance verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

ARTIFACTS = (
    "duplicates.json",
    "manifest.json",
    "matches.jsonl",
    "replays.json",
    "reports.json",
)
COUNTERS = (
    "replay_count",
    "unique_replay_count",
    "duplicate_replay_count",
    "report_count",
    "match_count",
)


class SnapshotError(ValueError):
    """A snapshot is missing, unsafe to read, or structurally invalid."""


def _read_artifact(root_fd: int, name: str, *, keep_bytes: bool = False) -> tuple[str, bytes | None]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise SnapshotError(f"{name}: expected a regular file")
            digest = hashlib.sha256()
            parts: list[bytes] = []
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                if keep_bytes:
                    parts.append(chunk)
            after = os.fstat(stream.fileno())
            logical = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise SnapshotError(f"{name}: changed while reading")
            if (before.st_dev, before.st_ino) != (logical.st_dev, logical.st_ino):
                raise SnapshotError(f"{name}: path changed while reading")
            return digest.hexdigest(), b"".join(parts) if keep_bytes else None
    except OSError as exc:
        raise SnapshotError(f"{name}: cannot read regular artifact: {exc.strerror or type(exc).__name__}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError(f"manifest.json: duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SnapshotError(f"manifest.json: invalid numeric constant {value}")


def _manifest_counts(raw: bytes) -> dict[str, int]:
    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("manifest.json: invalid UTF-8 or JSON") from exc
    if type(manifest) is not dict or type(manifest.get("schema_version")) is not int:
        raise SnapshotError("manifest.json: expected v1 object")
    if manifest["schema_version"] != 1:
        raise SnapshotError("manifest.json: unsupported schema version")
    counts: dict[str, int] = {}
    for key in COUNTERS:
        value = manifest.get(key)
        if type(value) is not int or value < 0:
            raise SnapshotError(f"manifest.json: {key} must be a nonnegative integer")
        counts[key] = value
    if counts["replay_count"] != counts["unique_replay_count"] + counts["duplicate_replay_count"]:
        raise SnapshotError("manifest.json: incoherent replay counts")
    if counts["match_count"] > min(counts["unique_replay_count"], counts["report_count"]):
        raise SnapshotError("manifest.json: incoherent match count")
    return counts


def snapshot(root: Path) -> dict[str, Any]:
    """Read one coherent directory identity, never mixing swapped roots."""
    try:
        original = root.lstat()
    except OSError as exc:
        raise SnapshotError(
            f"invalid snapshot directory: {exc.strerror or type(exc).__name__}"
        ) from exc
    if not stat.S_ISDIR(original.st_mode):
        raise SnapshotError("snapshot root must be a real directory, not a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise SnapshotError(
            f"invalid snapshot directory: {exc.strerror or type(exc).__name__}"
        ) from exc
    try:
        opened = os.fstat(root_fd)
        if not stat.S_ISDIR(opened.st_mode):
            raise SnapshotError("snapshot root is not a directory")
        if (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino):
            raise SnapshotError("snapshot root changed before read")
        hashes: dict[str, str] = {}
        manifest_bytes: bytes | None = None
        for name in ARTIFACTS:
            digest, raw = _read_artifact(
                root_fd, name, keep_bytes=(name == "manifest.json")
            )
            hashes[name] = digest
            if name == "manifest.json":
                manifest_bytes = raw
        latest = os.stat(root, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (latest.st_dev, latest.st_ino):
            raise SnapshotError("snapshot root changed while reading")
        if manifest_bytes is None:
            raise SnapshotError("manifest.json: missing evidence")
        return {"sha256": hashes, "counts": _manifest_counts(manifest_bytes)}
    except OSError as exc:
        raise SnapshotError(
            f"invalid snapshot directory: {exc.strerror or type(exc).__name__}"
        ) from exc
    finally:
        os.close(root_fd)

def compare(left: Path, right: Path) -> dict[str, Any]:
    before, after = snapshot(left), snapshot(right)
    changed = [name for name in ARTIFACTS if before["sha256"][name] != after["sha256"][name]]
    count_delta = {
        key: after["counts"][key] - before["counts"][key]
        for key in COUNTERS
        if before["counts"][key] != after["counts"][key]
    }
    return {
        "schema": "haxlab-m0-snapshot-comparison-v1",
        "identical": not changed,
        "changed_artifacts": changed,
        "count_delta": count_delta,
        "before": before,
        "after": after,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="First completed M0 import directory")
    parser.add_argument("after", type=Path, help="Second completed M0 import directory")
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit 1 if any published artifact bytes differ (default: report only)",
    )
    args = parser.parse_args(argv)
    try:
        result = compare(args.before, args.after)
    except SnapshotError as exc:
        print(f"M0_SNAPSHOT_INVALID: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if args.fail_on_drift and not result["identical"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
