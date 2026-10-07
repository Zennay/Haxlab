from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from haxlab.ingestion.dataset_receipt import (
    M0_ARTIFACTS,
    DatasetReceiptError,
    build_dataset_receipt,
)


class DatasetReceiptStoreError(ValueError):
    """Raised when receipt evidence cannot be published fail-safely."""


def receipt_file_bytes(receipt: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {**receipt, "ok": True},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _open_output_parent(path: Path) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    odirectory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or odirectory is None:
        raise DatasetReceiptStoreError(
            "O_NOFOLLOW and O_DIRECTORY are required for receipt publication"
        )

    parent = path.parent if path.parent != Path("") else Path(".")
    try:
        return os.open(parent, os.O_RDONLY | nofollow | odirectory)
    except OSError as exc:
        raise DatasetReceiptStoreError(
            f"receipt output parent is unsafe or unreadable: {parent}"
        ) from exc


def _reject_dataset_artifact_destination(
    dataset_root: Path,
    parent_fd: int,
    output_name: str,
) -> None:
    if output_name not in M0_ARTIFACTS:
        return

    nofollow = getattr(os, "O_NOFOLLOW", None)
    odirectory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or odirectory is None:
        raise DatasetReceiptStoreError(
            "O_NOFOLLOW and O_DIRECTORY are required for receipt publication"
        )

    root_fd = -1
    try:
        root_fd = os.open(
            Path(dataset_root),
            os.O_RDONLY | nofollow | odirectory,
        )
        root_stat = os.fstat(root_fd)
        parent_stat = os.fstat(parent_fd)
    except OSError as exc:
        raise DatasetReceiptStoreError(
            "dataset root changed before receipt publication"
        ) from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)

    if (
        root_stat.st_dev == parent_stat.st_dev
        and root_stat.st_ino == parent_stat.st_ino
    ):
        raise DatasetReceiptStoreError(
            f"receipt output would overwrite dataset artifact: {output_name}"
        )


def _create_temp_file(parent_fd: int, output_name: str) -> tuple[int, str]:
    for _ in range(16):
        temp_name = f".{output_name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            return fd, temp_name
        except FileExistsError:
            continue
    raise DatasetReceiptStoreError("unable to allocate unique receipt tempfile")


def store_dataset_receipt(
    dataset_root: Path,
    output_path: Path,
) -> dict[str, object]:
    """Publish exact M0 receipt evidence without exposing partial JSON bytes."""

    output_path = Path(output_path)
    if output_path.name in {"", ".", ".."}:
        raise DatasetReceiptStoreError("receipt output must name a file")

    receipt = build_dataset_receipt(Path(dataset_root))
    payload = receipt_file_bytes(receipt)

    parent_fd = _open_output_parent(output_path)
    _reject_dataset_artifact_destination(
        Path(dataset_root),
        parent_fd,
        output_path.name,
    )
    temp_fd = -1
    temp_name: str | None = None
    try:
        temp_fd, temp_name = _create_temp_file(parent_fd, output_path.name)
        with os.fdopen(temp_fd, "wb", closefd=True) as handle:
            temp_fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temp_name,
            output_path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temp_name = None
        os.fsync(parent_fd)
    except OSError as exc:
        raise DatasetReceiptStoreError(
            f"receipt publication failed: {output_path}"
        ) from exc
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)

    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.dataset_receipt_store"
    )
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output_receipt", type=Path)
    args = parser.parse_args()

    try:
        receipt = store_dataset_receipt(
            args.dataset_root,
            args.output_receipt,
        )
    except (DatasetReceiptError, DatasetReceiptStoreError) as exc:
        print(
            json.dumps(
                {
                    "schema": "haxlab-m0-dataset-receipt-store-v1",
                    "ok": False,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "schema": "haxlab-m0-dataset-receipt-store-v1",
                "ok": True,
                "receipt_sha256": receipt["receipt_sha256"],
                "output": str(args.output_receipt),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
