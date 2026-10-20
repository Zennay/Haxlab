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
# Keep the producer compatible with the verifier's bounded receipt reader.
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024
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
            # The canonical verifier forbids literal backslashes in any receipt
            # path. Reject at the source boundary, including parent directories,
            # rather than emitting evidence the verifier cannot accept (#407).
            if "\\" in relative:
                raise SourceBundleReceiptError(
                    "source_path_noncanonical_backslash"
                )
            # Linux can enumerate names containing undecodable filename bytes
            # through Python's surrogateescape convention. Those surrogate
            # code points cannot be encoded as UTF-8 by our canonical JSON
            # serializer; reject with a stable source reason, not a traceback
            # or silently rewritten evidence path.
            if any(0xD800 <= ord(char) <= 0xDFFF for char in relative):
                raise SourceBundleReceiptError(
                    "source_path_noncanonical_unicode"
                )
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
) -> tuple[int, str, tuple[int, int, int, int, int]]:
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
                try:
                    chunk = os.read(fd, 1024 * 1024)
                except OSError as exc:
                    raise SourceBundleReceiptError(
                        f"source_file_read_failed:{relative}:{exc}"
                    ) from exc
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

        return total, digest.hexdigest(), _stat_identity(after)
    finally:
        os.close(parent_fd)


def _assert_hashed_file_identity(
    root_fd: int,
    parts: tuple[str, ...],
    relative: str,
    directory_identities: dict[str, tuple[int, int]],
    expected_identity: tuple[int, int, int, int, int],
) -> None:
    parent_fd = _open_bound_parent(root_fd, parts[:-1], directory_identities)
    try:
        try:
            current = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleReceiptError(
                f"source_file_changed_after_hash:{relative}:{exc}"
            ) from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or _stat_identity(current) != expected_identity
        ):
            raise SourceBundleReceiptError(
                f"source_file_changed_after_hash:{relative}"
            )
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
        file_identities: dict[str, tuple[int, int, int, int, int]] = {}

        for kind, relative, parts in inventory:
            size_bytes, sha256, file_identity = _read_regular_file(
                root_fd,
                parts,
                relative,
                directory_identities,
            )
            file_identities[relative] = file_identity
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
        for _kind, relative, parts in inventory:
            _assert_hashed_file_identity(
                root_fd,
                parts,
                relative,
                directory_identities,
                file_identities[relative],
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
        # The downstream verifier refuses receipts larger than 32 MiB.
        # Never claim a source receipt that cannot be consumed by its own
        # canonical verifier. This is a post-inventory refusal, not truncation.
        if len(_canonical_bytes(payload)) > _MAX_RECEIPT_BYTES:
            raise SourceBundleReceiptError("source_receipt_too_large")
        return payload
    finally:
        os.close(root_fd)


def _save_private_receipt(
    receipt: dict[str, Any], destination: Path, export_root: Path
) -> None:
    """Publish a verifier-readable receipt to an explicit new 0600 file only.

    Do not write it under the scanned export root: the next scan would
    accidentally treat the receipt itself as newly imported Discord JSON.
    The parent must already exist without symlink components. Never overwrite
    or follow a destination link and never emit private paths to stdout.
    """
    output_path = _absolute_root(destination)
    source_root = _absolute_root(export_root)
    if output_path == source_root or output_path.is_relative_to(source_root):
        raise SourceBundleReceiptError("source_receipt_output_inside_source_root")
    if output_path.suffix.lower() != ".json":
        raise SourceBundleReceiptError("source_receipt_output_requires_json_suffix")

    encoded = _canonical_bytes(receipt)
    if len(encoded) > _MAX_RECEIPT_BYTES:
        raise SourceBundleReceiptError("source_receipt_too_large")

    try:
        parent_fd, _ = _open_root_directory(output_path.parent)
    except SourceBundleReceiptError as exc:
        raise SourceBundleReceiptError("source_receipt_output_parent_invalid") from exc

    fd: int | None = None
    created_identity: tuple[int, int] | None = None
    complete = False
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(output_path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise SourceBundleReceiptError("source_receipt_output_exists") from exc
        except OSError as exc:
            raise SourceBundleReceiptError("source_receipt_output_open_failed") from exc

        opened = os.fstat(fd)
        created_identity = _directory_identity(opened)
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("zero-byte write")
                remaining = remaining[written:]
            os.fsync(fd)
            current = os.stat(
                output_path.name, dir_fd=parent_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(current.st_mode) or (
                _directory_identity(current) != created_identity
            ):
                raise OSError("destination identity changed")
            os.fsync(parent_fd)
            complete = True
        except OSError as exc:
            raise SourceBundleReceiptError("source_receipt_output_write_failed") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if not complete and created_identity is not None:
            # Remove only our own incomplete newly created inode. Never delete
            # an unrelated file substituted by another process.
            try:
                current = os.stat(
                    output_path.name, dir_fd=parent_fd, follow_symlinks=False
                )
                if _directory_identity(current) == created_identity:
                    os.unlink(output_path.name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.source_bundle_receipt"
    )
    parser.add_argument("export_root", type=Path)
    parser.add_argument(
        "--output", type=Path,
        help="write a new private JSON receipt outside the source root (never overwrite)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        receipt = create_source_bundle_receipt(args.export_root)
        if args.output is not None:
            _save_private_receipt(receipt, args.output, args.export_root)
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

    visible = receipt if args.output is None else {
        "schema": SCHEMA,
        "clean": True,
        "saved_private_receipt": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "file_count": receipt["file_count"],
        "hbr2_count": receipt["hbr2_count"],
        "discord_json_count": receipt["discord_json_count"],
        "total_size_bytes": receipt["total_size_bytes"],
    }
    print(
        json.dumps(
            visible,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
