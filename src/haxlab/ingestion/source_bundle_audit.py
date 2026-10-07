from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


AUDIT_SCHEMA = "haxlab-source-bundle-audit-v1"
RECEIPT_SCHEMA = "haxlab-source-bundle-receipt-v1"
_TOP_LEVEL_KEYS = {
    "schema",
    "file_count",
    "hbr2_count",
    "discord_json_count",
    "total_size_bytes",
    "files",
    "receipt_sha256",
}
_FILE_KEYS = {"kind", "path", "size_bytes", "sha256"}
_KINDS = {"hbr2", "discord_json"}


class SourceBundleAuditError(ValueError):
    """Raised when published source-bundle evidence cannot be trusted."""


def _kind_for_name(name: str) -> str | None:
    if name.lower().endswith(".hbr2"):
        return "hbr2"
    if name.endswith(".json"):
        return "discord_json"
    return None


def _reject_constant(value: str) -> Any:
    raise SourceBundleAuditError(f"receipt_non_finite_number:{value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceBundleAuditError(f"receipt_duplicate_key:{key}")
        result[key] = value
    return result


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stable_regular_bytes(path: Path, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise SourceBundleAuditError(f"{label}_unreadable:{exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise SourceBundleAuditError(f"{label}_symlink")
    if not stat.S_ISREG(before.st_mode):
        raise SourceBundleAuditError(f"{label}_not_regular")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceBundleAuditError(f"{label}_open_failed:{exc}") from exc

    chunks: list[bytes] = []
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise SourceBundleAuditError(f"{label}_not_regular")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SourceBundleAuditError(f"{label}_identity_changed")
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != identity:
            raise SourceBundleAuditError(f"{label}_changed_during_read")
    finally:
        os.close(fd)

    try:
        final = path.lstat()
    except OSError as exc:
        raise SourceBundleAuditError(f"{label}_changed_after_read:{exc}") from exc
    final_identity = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
        final.st_ctime_ns,
    )
    if stat.S_ISLNK(final.st_mode) or final_identity != identity:
        raise SourceBundleAuditError(f"{label}_changed_after_read")

    data = b"".join(chunks)
    if len(data) != identity[2]:
        raise SourceBundleAuditError(f"{label}_size_changed_during_read")
    return data


def _inventory_paths(root: Path) -> tuple[tuple[str, str, Path], ...]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise SourceBundleAuditError(f"source_root_unreadable:{exc}") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise SourceBundleAuditError("source_root_symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise SourceBundleAuditError("source_root_not_directory")

    def walk_error(exc: OSError) -> None:
        raise SourceBundleAuditError(f"source_inventory_failed:{exc}") from exc

    entries: list[tuple[str, str, Path]] = []
    try:
        for current, dirnames, filenames in os.walk(
            root,
            topdown=True,
            onerror=walk_error,
            followlinks=False,
        ):
            current_path = Path(current)
            for dirname in dirnames:
                child = current_path / dirname
                relative = child.relative_to(root).as_posix()
                try:
                    child_stat = child.lstat()
                except OSError as exc:
                    raise SourceBundleAuditError(
                        f"source_directory_unreadable:{relative}:{exc}"
                    ) from exc
                if stat.S_ISLNK(child_stat.st_mode):
                    raise SourceBundleAuditError(f"source_directory_symlink:{relative}")
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise SourceBundleAuditError(
                        f"source_directory_not_directory:{relative}"
                    )

            for filename in filenames:
                kind = _kind_for_name(filename)
                if kind is None:
                    continue
                path = current_path / filename
                entries.append((kind, path.relative_to(root).as_posix(), path))
    except OSError as exc:
        raise SourceBundleAuditError(f"source_inventory_failed:{exc}") from exc

    entries.sort(key=lambda item: (item[1].casefold(), item[1]))
    return tuple(entries)


def _load_receipt(path: Path) -> dict[str, Any]:
    raw = _stable_regular_bytes(path, "receipt_file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceBundleAuditError("receipt_invalid_utf8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except SourceBundleAuditError:
        raise
    except json.JSONDecodeError as exc:
        raise SourceBundleAuditError(f"receipt_invalid_json:{exc.msg}") from exc
    if type(value) is not dict:
        raise SourceBundleAuditError("receipt_not_object")
    return value


def _require_native_nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise SourceBundleAuditError(f"receipt_invalid_{field}")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise SourceBundleAuditError(f"receipt_invalid_{field}")
    if value != value.lower() or any(char not in "0123456789abcdef" for char in value):
        raise SourceBundleAuditError(f"receipt_invalid_{field}")
    return value


def _require_relative_path(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise SourceBundleAuditError("receipt_invalid_path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value:
        raise SourceBundleAuditError(f"receipt_invalid_path:{value}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise SourceBundleAuditError(f"receipt_invalid_path:{value}")
    return value


def _validate_receipt(value: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if set(value) != _TOP_LEVEL_KEYS:
        raise SourceBundleAuditError("receipt_top_level_fields_mismatch")
    if value["schema"] != RECEIPT_SCHEMA:
        raise SourceBundleAuditError("receipt_schema_mismatch")
    if type(value["files"]) is not list:
        raise SourceBundleAuditError("receipt_files_not_list")

    expected_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(value["files"]):
        if type(item) is not dict or set(item) != _FILE_KEYS:
            raise SourceBundleAuditError(f"receipt_file_fields_mismatch:{index}")
        kind = item["kind"]
        if kind not in _KINDS:
            raise SourceBundleAuditError(f"receipt_invalid_kind:{index}")
        path = _require_relative_path(item["path"])
        if path in seen_paths:
            raise SourceBundleAuditError(f"receipt_duplicate_path:{path}")
        seen_paths.add(path)
        size_bytes = _require_native_nonnegative_int(item["size_bytes"], "size_bytes")
        sha256 = _require_sha256(item["sha256"], "file_sha256")
        expected_files.append(
            {
                "kind": kind,
                "path": path,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )

    canonical_order = sorted(
        expected_files,
        key=lambda item: (item["path"].casefold(), item["path"]),
    )
    if expected_files != canonical_order:
        raise SourceBundleAuditError("receipt_files_not_canonical_order")

    file_count = _require_native_nonnegative_int(value["file_count"], "file_count")
    hbr2_count = _require_native_nonnegative_int(value["hbr2_count"], "hbr2_count")
    discord_json_count = _require_native_nonnegative_int(
        value["discord_json_count"], "discord_json_count"
    )
    total_size_bytes = _require_native_nonnegative_int(
        value["total_size_bytes"], "total_size_bytes"
    )
    if file_count != len(expected_files):
        raise SourceBundleAuditError("receipt_file_count_mismatch")
    if hbr2_count != sum(item["kind"] == "hbr2" for item in expected_files):
        raise SourceBundleAuditError("receipt_hbr2_count_mismatch")
    if discord_json_count != sum(
        item["kind"] == "discord_json" for item in expected_files
    ):
        raise SourceBundleAuditError("receipt_discord_json_count_mismatch")
    if total_size_bytes != sum(item["size_bytes"] for item in expected_files):
        raise SourceBundleAuditError("receipt_total_size_mismatch")

    receipt_sha256 = _require_sha256(value["receipt_sha256"], "receipt_sha256")
    unsigned = dict(value)
    del unsigned["receipt_sha256"]
    actual_receipt_sha256 = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    if receipt_sha256 != actual_receipt_sha256:
        raise SourceBundleAuditError("receipt_digest_mismatch")
    return expected_files, receipt_sha256


def audit_source_bundle(export_root: Path, receipt_path: Path) -> dict[str, Any]:
    """Validate a published receipt and bind it to the current immutable source bytes."""
    receipt_value = _load_receipt(Path(receipt_path))
    expected_files, receipt_sha256 = _validate_receipt(receipt_value)

    root = Path(export_root)
    initial_inventory = _inventory_paths(root)
    actual_files: list[dict[str, Any]] = []
    for kind, relative, path in initial_inventory:
        data = _stable_regular_bytes(path, f"source_file:{relative}")
        actual_files.append(
            {
                "kind": kind,
                "path": relative,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    final_inventory = _inventory_paths(root)
    before_identity = tuple((kind, relative) for kind, relative, _ in initial_inventory)
    after_identity = tuple((kind, relative) for kind, relative, _ in final_inventory)
    if before_identity != after_identity:
        raise SourceBundleAuditError("source_inventory_changed_during_audit")

    expected_identity = [(item["kind"], item["path"]) for item in expected_files]
    actual_identity = [(item["kind"], item["path"]) for item in actual_files]
    if expected_identity != actual_identity:
        raise SourceBundleAuditError("source_inventory_mismatch")

    for expected, actual in zip(expected_files, actual_files, strict=True):
        if expected["size_bytes"] != actual["size_bytes"]:
            raise SourceBundleAuditError(f"source_size_mismatch:{expected['path']}")
        if expected["sha256"] != actual["sha256"]:
            raise SourceBundleAuditError(f"source_sha256_mismatch:{expected['path']}")

    return {
        "schema": AUDIT_SCHEMA,
        "clean": True,
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_sha256": receipt_sha256,
        "file_count": len(actual_files),
        "hbr2_count": sum(item["kind"] == "hbr2" for item in actual_files),
        "discord_json_count": sum(
            item["kind"] == "discord_json" for item in actual_files
        ),
        "total_size_bytes": sum(item["size_bytes"] for item in actual_files),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.source_bundle_audit"
    )
    parser.add_argument("export_root", type=Path)
    parser.add_argument("receipt_path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = audit_source_bundle(args.export_root, args.receipt_path)
    except SourceBundleAuditError as exc:
        print(
            json.dumps(
                {"schema": AUDIT_SCHEMA, "clean": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
