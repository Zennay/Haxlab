from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from haxlab.ingestion.source_bundle_receipt import (
    SCHEMA as SOURCE_RECEIPT_SCHEMA,
    SourceBundleReceiptError,
    create_source_bundle_receipt,
)


VERIFY_SCHEMA = "haxlab-source-bundle-verification-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_KEYS = {
    "schema",
    "file_count",
    "hbr2_count",
    "discord_json_count",
    "total_size_bytes",
    "files",
    "receipt_sha256",
}
_FILE_KEYS = {"kind", "path", "size_bytes", "sha256"}
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024


class SourceBundleVerifyError(ValueError):
    """Raised when expected source-receipt evidence cannot be trusted."""


ReceiptBinding = tuple[tuple[int, ...], ...]


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise SourceBundleVerifyError("receipt_directory_descriptors_unsupported")
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _file_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise SourceBundleVerifyError("receipt_nofollow_unsupported")
    return os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SourceBundleVerifyError(f"duplicate_json_key:{key}")
        value[key] = item
    return value


def _invalid_constant(value: str) -> None:
    raise SourceBundleVerifyError(f"invalid_json_constant:{value}")


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_receipt_parent(path: Path) -> tuple[int, str, ReceiptBinding]:
    logical_path = Path(os.path.abspath(os.fspath(path)))
    parts = logical_path.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise SourceBundleVerifyError("receipt_path_invalid")

    flags = _directory_open_flags()
    try:
        current_fd = os.open(os.sep, flags)
    except OSError as exc:
        raise SourceBundleVerifyError(f"receipt_parent_open_failed:{exc}") from exc

    identities: list[tuple[int, int]] = []
    try:
        root_stat = os.fstat(current_fd)
        identities.append((root_stat.st_dev, root_stat.st_ino))
        traversed: list[str] = []
        for component in parts[1:-1]:
            traversed.append(component)
            relative = "/".join(traversed)
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError as exc:
                raise SourceBundleVerifyError(
                    f"receipt_parent_unreadable:{relative}:{exc}"
                ) from exc
            if stat.S_ISLNK(before.st_mode):
                raise SourceBundleVerifyError(f"receipt_parent_symlink:{relative}")
            if not stat.S_ISDIR(before.st_mode):
                raise SourceBundleVerifyError(
                    f"receipt_parent_not_directory:{relative}"
                )
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                raise SourceBundleVerifyError(
                    f"receipt_parent_open_failed:{relative}:{exc}"
                ) from exc
            try:
                opened = os.fstat(next_fd)
                before_identity = (before.st_dev, before.st_ino)
                opened_identity = (opened.st_dev, opened.st_ino)
                if not stat.S_ISDIR(opened.st_mode) or opened_identity != before_identity:
                    raise SourceBundleVerifyError(
                        f"receipt_parent_identity_changed:{relative}"
                    )
            except BaseException:
                os.close(next_fd)
                raise
            identities.append(opened_identity)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1], tuple(identities)
    except BaseException:
        os.close(current_fd)
        raise


def _receipt_path_binding(path: Path) -> ReceiptBinding:
    parent_fd, name, parent_binding = _open_receipt_parent(path)
    try:
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleVerifyError(
                f"receipt_path_unreadable:{exc}"
            ) from exc
        if stat.S_ISLNK(current.st_mode):
            raise SourceBundleVerifyError("receipt_path_symlink")
        if not stat.S_ISREG(current.st_mode):
            raise SourceBundleVerifyError("receipt_path_not_regular")
        return parent_binding + (_stat_identity(current),)
    finally:
        os.close(parent_fd)


def _read_receipt_bytes(path: Path) -> tuple[bytes, ReceiptBinding]:
    parent_fd, name, parent_binding = _open_receipt_parent(path)
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleVerifyError(f"receipt_unreadable:{exc}") from exc
        if stat.S_ISLNK(before.st_mode):
            raise SourceBundleVerifyError("receipt_symlink")
        if not stat.S_ISREG(before.st_mode):
            raise SourceBundleVerifyError("receipt_not_regular")
        if before.st_size > _MAX_RECEIPT_BYTES:
            raise SourceBundleVerifyError("receipt_too_large")

        try:
            fd = os.open(name, _file_open_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise SourceBundleVerifyError(f"receipt_open_failed:{exc}") from exc

        chunks: list[bytes] = []
        total = 0
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise SourceBundleVerifyError("receipt_not_regular")
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SourceBundleVerifyError("receipt_identity_changed")

            while True:
                chunk = os.read(
                    fd,
                    min(1024 * 1024, _MAX_RECEIPT_BYTES + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_RECEIPT_BYTES:
                    raise SourceBundleVerifyError("receipt_too_large")

            after = os.fstat(fd)
            if _stat_identity(after) != _stat_identity(opened) or total != after.st_size:
                raise SourceBundleVerifyError("receipt_changed_during_read")
        finally:
            os.close(fd)

        try:
            final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise SourceBundleVerifyError(
                f"receipt_changed_after_read:{exc}"
            ) from exc
        if stat.S_ISLNK(final.st_mode) or _stat_identity(final) != _stat_identity(after):
            raise SourceBundleVerifyError("receipt_changed_after_read")

        binding = parent_binding + (_stat_identity(after),)
        try:
            logical_binding = _receipt_path_binding(path)
        except SourceBundleVerifyError as exc:
            raise SourceBundleVerifyError(
                "receipt_logical_path_changed_during_read"
            ) from exc
        if logical_binding != binding:
            raise SourceBundleVerifyError(
                "receipt_logical_path_changed_during_read"
            )
        return b"".join(chunks), binding
    finally:
        os.close(parent_fd)


def _load_receipt(
    path: Path,
) -> tuple[dict[str, Any], bytes, ReceiptBinding]:
    raw, binding = _read_receipt_bytes(path)
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceBundleVerifyError(f"invalid_receipt_json:{exc}") from exc
    if not isinstance(payload, dict):
        raise SourceBundleVerifyError("receipt_not_object")
    return payload, raw, binding


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _native_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SourceBundleVerifyError("invalid_file_path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix():
        raise SourceBundleVerifyError(f"invalid_file_path:{value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SourceBundleVerifyError(f"invalid_file_path:{value}")
    return value


def validate_source_receipt(payload: dict[str, Any]) -> None:
    if set(payload) != _EXPECTED_KEYS:
        missing = sorted(_EXPECTED_KEYS - set(payload))
        unknown = sorted(set(payload) - _EXPECTED_KEYS)
        raise SourceBundleVerifyError(
            f"receipt_fields_mismatch:missing={','.join(missing)}:unknown={','.join(unknown)}"
        )
    if payload["schema"] != SOURCE_RECEIPT_SCHEMA:
        raise SourceBundleVerifyError("receipt_schema_mismatch")
    for key in ("file_count", "hbr2_count", "discord_json_count", "total_size_bytes"):
        if not _native_nonnegative_int(payload[key]):
            raise SourceBundleVerifyError(f"invalid_{key}")
    if not isinstance(payload["receipt_sha256"], str) or not _SHA256.fullmatch(
        payload["receipt_sha256"]
    ):
        raise SourceBundleVerifyError("invalid_receipt_sha256")
    if not isinstance(payload["files"], list):
        raise SourceBundleVerifyError("files_not_list")

    seen: set[str] = set()
    normalized: list[tuple[str, str, int, str]] = []
    hbr2_count = 0
    json_count = 0
    total_size = 0

    for index, item in enumerate(payload["files"]):
        if not isinstance(item, dict) or set(item) != _FILE_KEYS:
            raise SourceBundleVerifyError(f"invalid_file_entry:{index}")
        kind = item["kind"]
        path = _validate_relative_path(item["path"])
        size = item["size_bytes"]
        sha256 = item["sha256"]
        if kind not in {"hbr2", "discord_json"}:
            raise SourceBundleVerifyError(f"invalid_file_kind:{path}")
        if kind == "hbr2" and not path.lower().endswith(".hbr2"):
            raise SourceBundleVerifyError(f"file_kind_path_mismatch:{path}")
        if kind == "discord_json" and not path.endswith(".json"):
            raise SourceBundleVerifyError(f"file_kind_path_mismatch:{path}")
        if not _native_nonnegative_int(size):
            raise SourceBundleVerifyError(f"invalid_file_size:{path}")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise SourceBundleVerifyError(f"invalid_file_sha256:{path}")
        if path in seen:
            raise SourceBundleVerifyError(f"duplicate_file_path:{path}")
        seen.add(path)
        normalized.append((path, kind, size, sha256))
        total_size += size
        if kind == "hbr2":
            hbr2_count += 1
        else:
            json_count += 1

    expected_order = sorted(normalized, key=lambda row: (row[0].casefold(), row[0]))
    if normalized != expected_order:
        raise SourceBundleVerifyError("files_not_canonically_ordered")
    if payload["file_count"] != len(normalized):
        raise SourceBundleVerifyError("file_count_mismatch")
    if payload["hbr2_count"] != hbr2_count:
        raise SourceBundleVerifyError("hbr2_count_mismatch")
    if payload["discord_json_count"] != json_count:
        raise SourceBundleVerifyError("discord_json_count_mismatch")
    if payload["total_size_bytes"] != total_size:
        raise SourceBundleVerifyError("total_size_bytes_mismatch")

    unsigned = dict(payload)
    claimed = unsigned.pop("receipt_sha256")
    actual = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    if claimed != actual:
        raise SourceBundleVerifyError("receipt_self_digest_mismatch")


def verify_source_bundle(export_root: Path, receipt_path: Path) -> dict[str, Any]:
    expected, first_raw, receipt_binding = _load_receipt(Path(receipt_path))
    validate_source_receipt(expected)

    current = create_source_bundle_receipt(Path(export_root))

    second_raw, second_binding = _read_receipt_bytes(Path(receipt_path))
    if second_raw != first_raw:
        raise SourceBundleVerifyError("receipt_changed_during_verification")
    if second_binding != receipt_binding:
        raise SourceBundleVerifyError(
            "receipt_path_identity_changed_during_verification"
        )

    expected_by_path = {item["path"]: item for item in expected["files"]}
    current_by_path = {item["path"]: item for item in current["files"]}
    missing = sorted(set(expected_by_path) - set(current_by_path))
    unexpected = sorted(set(current_by_path) - set(expected_by_path))
    changed = sorted(
        path
        for path in set(expected_by_path) & set(current_by_path)
        if expected_by_path[path] != current_by_path[path]
    )
    clean = expected == current
    return {
        "schema": VERIFY_SCHEMA,
        "clean": clean,
        "expected_receipt_sha256": expected["receipt_sha256"],
        "current_receipt_sha256": current["receipt_sha256"],
        "missing_sources": missing,
        "unexpected_sources": unexpected,
        "changed_sources": changed,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.source_bundle_verify"
    )
    parser.add_argument("export_root", type=Path)
    parser.add_argument("receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = verify_source_bundle(args.export_root, args.receipt)
    except (SourceBundleVerifyError, SourceBundleReceiptError) as exc:
        print(
            json.dumps(
                {"schema": VERIFY_SCHEMA, "clean": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
