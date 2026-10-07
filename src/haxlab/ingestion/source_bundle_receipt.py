from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "haxlab-source-bundle-receipt-v1"


class SourceBundleReceiptError(ValueError):
    """Raised when immutable import-source evidence cannot be trusted."""


def _kind_for_name(name: str) -> str | None:
    if name.lower().endswith(".hbr2"):
        return "hbr2"
    if name.endswith(".json"):
        return "discord_json"
    return None


def _inventory_paths(root: Path) -> tuple[tuple[str, str, Path], ...]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise SourceBundleReceiptError(f"source_root_unreadable:{exc}") from exc

    if stat.S_ISLNK(root_stat.st_mode):
        raise SourceBundleReceiptError("source_root_symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise SourceBundleReceiptError("source_root_not_directory")

    entries: list[tuple[str, str, Path]] = []
    try:
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)

            for dirname in dirnames:
                child = current_path / dirname
                try:
                    child_stat = child.lstat()
                except OSError as exc:
                    raise SourceBundleReceiptError(
                        f"source_directory_unreadable:{child.relative_to(root).as_posix()}:{exc}"
                    ) from exc
                if stat.S_ISLNK(child_stat.st_mode):
                    raise SourceBundleReceiptError(
                        f"source_directory_symlink:{child.relative_to(root).as_posix()}"
                    )
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise SourceBundleReceiptError(
                        f"source_directory_not_directory:{child.relative_to(root).as_posix()}"
                    )

            for filename in filenames:
                kind = _kind_for_name(filename)
                if kind is None:
                    continue
                path = current_path / filename
                relative = path.relative_to(root).as_posix()
                entries.append((kind, relative, path))
    except OSError as exc:
        raise SourceBundleReceiptError(f"source_inventory_failed:{exc}") from exc

    entries.sort(key=lambda item: (item[1].casefold(), item[1]))
    return tuple(entries)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_regular_file(path: Path, relative: str) -> tuple[int, str]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise SourceBundleReceiptError(
            f"source_file_unreadable:{relative}:{exc}"
        ) from exc

    if stat.S_ISLNK(before.st_mode):
        raise SourceBundleReceiptError(f"source_file_symlink:{relative}")
    if not stat.S_ISREG(before.st_mode):
        raise SourceBundleReceiptError(f"source_file_not_regular:{relative}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceBundleReceiptError(
            f"source_file_open_failed:{relative}:{exc}"
        ) from exc

    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise SourceBundleReceiptError(f"source_file_not_regular:{relative}")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SourceBundleReceiptError(f"source_file_identity_changed:{relative}")

        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)

        after = os.fstat(fd)
        if _stat_identity(after) != _stat_identity(opened) or total != after.st_size:
            raise SourceBundleReceiptError(f"source_file_changed_during_read:{relative}")
    finally:
        os.close(fd)

    try:
        final = path.lstat()
    except OSError as exc:
        raise SourceBundleReceiptError(
            f"source_file_changed_after_read:{relative}:{exc}"
        ) from exc
    if stat.S_ISLNK(final.st_mode) or _stat_identity(final) != _stat_identity(after):
        raise SourceBundleReceiptError(f"source_file_changed_after_read:{relative}")

    return total, digest.hexdigest()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def create_source_bundle_receipt(export_root: Path) -> dict[str, Any]:
    """Return deterministic identity evidence for every importer-relevant source file."""
    root = Path(export_root)
    inventory = _inventory_paths(root)

    files: list[dict[str, Any]] = []
    hbr2_count = 0
    discord_json_count = 0
    total_size_bytes = 0

    for kind, relative, path in inventory:
        size_bytes, sha256 = _read_regular_file(path, relative)
        files.append(
            {
                "kind": kind,
                "path": relative,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )
        total_size_bytes += size_bytes
        if kind == "hbr2":
            hbr2_count += 1
        else:
            discord_json_count += 1

    final_inventory = _inventory_paths(root)
    before_identity = tuple((kind, relative) for kind, relative, _ in inventory)
    after_identity = tuple((kind, relative) for kind, relative, _ in final_inventory)
    if after_identity != before_identity:
        raise SourceBundleReceiptError("source_inventory_changed_during_receipt")

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "file_count": len(files),
        "hbr2_count": hbr2_count,
        "discord_json_count": discord_json_count,
        "total_size_bytes": total_size_bytes,
        "files": files,
    }
    payload["receipt_sha256"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.source_bundle_receipt"
    )
    parser.add_argument("export_root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        receipt = create_source_bundle_receipt(args.export_root)
    except SourceBundleReceiptError as exc:
        print(
            json.dumps(
                {"schema": SCHEMA, "clean": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    print(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
