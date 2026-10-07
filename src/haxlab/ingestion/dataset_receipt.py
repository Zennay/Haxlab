from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO


RECEIPT_SCHEMA = "haxlab-m0-dataset-receipt-v1"
M0_ARTIFACTS = (
    "manifest.json",
    "replays.json",
    "duplicates.json",
    "reports.json",
    "matches.jsonl",
)


class DatasetReceiptError(ValueError):
    """Raised when the M0 dataset artifact set cannot be bound safely."""


def _open_regular_nofollow(root_fd: int, name: str) -> BinaryIO:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise DatasetReceiptError(
            "O_NOFOLLOW and O_NONBLOCK are required for dataset receipts"
        )

    flags = os.O_RDONLY | nofollow | nonblock
    fd = -1
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise DatasetReceiptError(f"artifact is not a regular file: {name}")
        return os.fdopen(fd, "rb", closefd=True)
    except FileNotFoundError as exc:
        if fd >= 0:
            os.close(fd)
        raise DatasetReceiptError(f"missing required artifact: {name}") from exc
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise DatasetReceiptError(f"unsafe or unreadable artifact: {name}") from exc
    except Exception:
        if fd >= 0:
            os.close(fd)
        raise


def _artifact_record(root_fd: int, name: str) -> dict[str, object]:
    hasher = hashlib.sha256()
    size_bytes = 0

    with _open_regular_nofollow(root_fd, name) as handle:
        before = os.fstat(handle.fileno())
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size_bytes += len(chunk)
            hasher.update(chunk)
        after = os.fstat(handle.fileno())

    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        before_identity != after_identity
        or size_bytes != after.st_size
    ):
        raise DatasetReceiptError(f"artifact changed while hashing: {name}")

    return {
        "name": name,
        "size_bytes": size_bytes,
        "sha256": hasher.hexdigest(),
    }


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_dataset_receipt(root: Path) -> dict[str, object]:
    """Bind the complete M0 publication set to exact immutable artifact bytes."""

    root = Path(root)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    odirectory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or odirectory is None:
        raise DatasetReceiptError(
            "O_NOFOLLOW and O_DIRECTORY are required for dataset receipts"
        )

    root_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | nofollow | odirectory)
        root_before = os.fstat(root_fd)
        if not stat.S_ISDIR(root_before.st_mode):
            raise DatasetReceiptError("dataset root is not a directory")

        artifacts = [_artifact_record(root_fd, name) for name in M0_ARTIFACTS]
        root_after = os.fstat(root_fd)
        root_before_identity = (
            root_before.st_dev,
            root_before.st_ino,
            root_before.st_size,
            root_before.st_mtime_ns,
            root_before.st_ctime_ns,
        )
        root_after_identity = (
            root_after.st_dev,
            root_after.st_ino,
            root_after.st_size,
            root_after.st_mtime_ns,
            root_after.st_ctime_ns,
        )
        if root_before_identity != root_after_identity:
            raise DatasetReceiptError(
                "dataset root changed while building receipt"
            )
    except FileNotFoundError as exc:
        raise DatasetReceiptError(f"dataset root does not exist: {root}") from exc
    except OSError as exc:
        raise DatasetReceiptError(
            f"dataset root is unsafe or unreadable: {root}"
        ) from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)

    core: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    receipt_sha256 = hashlib.sha256(_canonical_bytes(core)).hexdigest()

    return {
        **core,
        "receipt_sha256": receipt_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.dataset_receipt"
    )
    parser.add_argument(
        "dataset_root",
        type=Path,
        help="Directory containing the complete five-file M0 publication set.",
    )
    args = parser.parse_args()

    try:
        receipt = build_dataset_receipt(args.dataset_root)
    except DatasetReceiptError as exc:
        print(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "ok": False,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(json.dumps({**receipt, "ok": True}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
