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
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class SourceBundleReceiptError(ValueError):
    """Raised when immutable import-source evidence cannot be trusted."""


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise SourceBundleReceiptError("source_directory_descriptors_unsupported")
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _kind_for_name(name: str) -> str | None:
    if name.lower().endswith(".hbr2"):
        return "hbr2"
    if name.endswith(".json"):
        return "discord_json"
    return None


def _directory_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _absolute_root(root: Path) -> Path:
    return Path(os.path.abspath(os.fspath(root)))


def _open_root_directory(root: Path) -> tuple[int, Path]:
    logical_root = _absolute_root(root)
    try:
        root_stat = logical_root.lstat()
    except OSError as exc:
        raise SourceBundleReceiptError(f"source_root_unreadable:{exc}") from exc

    if stat.S_ISLNK(root_stat.st_mode):
        raise SourceBundleReceiptError("source_root_symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise SourceBundleReceiptError("source_root_not_directory")

    parts = logical_root.parts
    if not parts or parts[0] != os.sep:
        raise SourceBundleReceiptError("source_root_not_absolute")

    try:
        current_fd = os.open(os.sep, _directory_open_flags())
    except OSError as exc:
        raise SourceBundleReceiptError(f"source_root_open_failed:{exc}") from exc

    traversed: list[str] = []
    try:
        for component in parts[1:]:
            traversed.append(component)
            relative = "/".join(traversed)
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError as exc:
                raise SourceBundleReceiptError(
                    f"source_root_component_unreadable:{relative}:{exc}"
                ) from exc
            if stat.S_ISLNK(before.st_mode):
                raise SourceBundleReceiptError(
                    f"source_root_component_symlink:{relative}"
                )
            if not stat.S_ISDIR(before.st_mode):
                raise SourceBundleReceiptError(
                    f"source_root_component_not_directory:{relative}"
                )
            try:
                next_fd = os.open(component, _directory_open_flags(), dir_fd=current_fd)
            except OSError as exc:
                raise SourceBundleReceiptError(
                    f"source_root_component_open_failed:{relative}:{exc}"
                ) from exc
            try:
                opened = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or _directory_identity(opened) != _directory_identity(before)
                ):
                    raise SourceBundleReceiptError(
                        f"source_root_component_identity_changed:{relative}"
                    )
            except BaseException:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, logical_root
    except BaseException:
        os.close(current_fd)
        raise


def _inventory_paths(
    root_fd: int,
) -> tuple[
    tuple[tuple[str, str, tuple[str, ...]], ...],
    dict[str, tuple[int, int]],
]:
    entries: list[tuple[str, str, tuple[str, ...]]] = []
    directory_identities: dict[str, tuple[int, int]] = {}

    def walk(directory_fd: int, parts: tuple[str, ...]) -> None:
        relative_directory = "/".join(parts)
        try:
            opened_directory = os.fstat(directory_fd)
        except OSError as exc:
            raise SourceBundleReceiptError(
                f"source_directory_unreadable:{relative_directory}:{exc}"
            ) from exc
        if not stat.S_ISDIR(opened_directory.st_mode):
            raise SourceBundleReceiptError(
                f"source_directory_not_directory:{relative_directory}"
            )
        directory_identities[relative_directory] = _directory_identity(opened_directory)

        try:
            names = os.listdir(directory_fd)
        except OSError as exc:
            raise SourceBundleReceiptError(f"source_inventory_failed:{exc}") from exc

        for name in names:
            child_parts = parts + (name,)
            relative = "/".join(child_parts)
            try:
                child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise SourceBundleReceiptError(
                    f"source_inventory_failed:{relative}:{exc}"
                ) from exc

            if stat.S_ISLNK(child_stat.st_mode):
                try:
                    followed = os.stat(name, dir_fd=directory_fd, follow_symlinks=True)
                except OSError:
                    followed = None
                if followed is not None and stat.S_ISDIR(followed.st_mode):
                    raise SourceBundleReceiptError(
                        f"source_directory_symlink:{relative}"
                    )
                if _kind_for_name(name) is not None:
                    raise SourceBundleReceiptError(f"source_file_symlink:{relative}")
                continue

            if stat.S_ISDIR(child_stat.st_mode):
                try:
                    child_fd = os.open(name, _directory_open_flags(), dir_fd=directory_fd)
                except OSError as exc:
                    raise SourceBundleReceiptError(
                        f"source_directory_open_failed:{relative}:{exc}"
                    ) from exc
                try:
                    opened = os.fstat(child_fd)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or _directory_identity(opened) != _directory_identity(child_stat)
                    ):
                        raise SourceBundleReceiptError(
                            f"source_directory_identity_changed:{relative}"
                        )
                    walk(child_fd, child_parts)
                finally:
                    os.close(child_fd)
                continue

            kind = _kind_for_name(name)
            if kind is None:
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                raise SourceBundleReceiptError(
                    f"source_file_not_regular:{relative}"
                )
            entries.append((kind, relative, child_parts))

    walk(root_fd, ())
    entries.sort(key=lambda item: (item[1].casefold(), item[1]))
    return tuple(entries), directory_identities


def _open_bound_parent(
    root_fd: int,
    parts: tuple[str, ...],
    directory_identities: dict[str, tuple[int, int]],
) -> int:
    current_fd = os.dup(root_fd)
    try:
        root_identity = _directory_identity(os.fstat(current_fd))
        if directory_identities.get("") != root_identity:
            raise SourceBundleReceiptError("source_root_identity_changed")

        prefix: list[str] = []
        for component in parts:
            prefix.append(component)
            relative = "/".join(prefix)
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError as exc:
                raise SourceBundleReceiptError(
                    f"source_directory_unreadable:{relative}:{exc}"
                ) from exc
            if stat.S_ISLNK(before.st_mode):
                raise SourceBundleReceiptError(
                    f"source_directory_symlink:{relative}"
                )
            if not stat.S_ISDIR(before.st_mode):
                raise SourceBundleReceiptError(
                    f"source_directory_not_directory:{relative}"
                )
            expected = directory_identities.get(relative)
            if expected is None or _directory_identity(before) != expected:
                raise SourceBundleReceiptError(
                    f"source_directory_identity_changed:{relative}"
                )
            try:
                next_fd = os.open(component, _directory_open_flags(), dir_fd=current_fd)
            except OSError as exc:
                raise SourceBundleReceiptError(
                    f"source_directory_open_failed:{relative}:{exc}"
                ) from exc
            try:
                opened = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or _directory_identity(opened) != expected
                ):
                    raise SourceBundleReceiptError(
                        f"source_directory_identity_changed:{relative}"
                    )
            except BaseException:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _read_regular_file(
    root_fd: int,
    parts: tuple[str, ...],
    relative: str,
    directory_identities: dict[str, tuple[int, int]],
) -> tuple[int, str]:
    parent_fd = _open_bound_parent(root_fd, parts[:-1], directory_identities)
    name = parts[-1]
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleReceiptError(
                f"source_file_unreadable:{relative}:{exc}"
            ) from exc

        if stat.S_ISLNK(before.st_mode):
            raise SourceBundleReceiptError(f"source_file_symlink:{relative}")
        if not stat.S_ISREG(before.st_mode):
            raise SourceBundleReceiptError(f"source_file_not_regular:{relative}")

        try:
            fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
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
                raise SourceBundleReceiptError(
                    f"source_file_identity_changed:{relative}"
                )

            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)

            after = os.fstat(fd)
            if _stat_identity(after) != _stat_identity(opened) or total != after.st_size:
                raise SourceBundleReceiptError(
                    f"source_file_changed_during_read:{relative}"
                )
        finally:
            os.close(fd)

        try:
            final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleReceiptError(
                f"source_file_changed_after_read:{relative}:{exc}"
            ) from exc
        if stat.S_ISLNK(final.st_mode) or _stat_identity(final) != _stat_identity(after):
            raise SourceBundleReceiptError(
                f"source_file_changed_after_read:{relative}"
            )

        return total, digest.hexdigest()
    finally:
        os.close(parent_fd)


def _reopen_root_identity(logical_root: Path) -> tuple[int, int]:
    try:
        reopened_fd, _ = _open_root_directory(logical_root)
    except SourceBundleReceiptError as exc:
        raise SourceBundleReceiptError("source_root_changed_during_receipt") from exc
    try:
        return _directory_identity(os.fstat(reopened_fd))
    finally:
        os.close(reopened_fd)


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def create_source_bundle_receipt(export_root: Path) -> dict[str, Any]:
    """Return deterministic identity evidence for every importer-relevant source file."""
    root_fd, logical_root = _open_root_directory(Path(export_root))
    try:
        root_identity = _directory_identity(os.fstat(root_fd))
        inventory, directory_identities = _inventory_paths(root_fd)

        files: list[dict[str, Any]] = []
        hbr2_count = 0
        discord_json_count = 0
        total_size_bytes = 0

        for kind, relative, parts in inventory:
            size_bytes, sha256 = _read_regular_file(
                root_fd,
                parts,
                relative,
                directory_identities,
            )
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

        final_inventory, final_directory_identities = _inventory_paths(root_fd)
        before_identity = tuple((kind, relative) for kind, relative, _ in inventory)
        after_identity = tuple(
            (kind, relative) for kind, relative, _ in final_inventory
        )
        if (
            after_identity != before_identity
            or final_directory_identities != directory_identities
        ):
            raise SourceBundleReceiptError(
                "source_inventory_changed_during_receipt"
            )
        if _reopen_root_identity(logical_root) != root_identity:
            raise SourceBundleReceiptError("source_root_changed_during_receipt")

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
    finally:
        os.close(root_fd)


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
