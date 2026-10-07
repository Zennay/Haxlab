from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from haxlab.ingestion.dataset_receipt import (
    M0_ARTIFACTS,
    RECEIPT_SCHEMA,
    DatasetReceiptError,
    build_dataset_receipt,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT_BYTES = 64 * 1024


class DatasetReceiptVerificationError(ValueError):
    """Raised when stored receipt evidence is malformed or no longer matches."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetReceiptVerificationError(
                f"duplicate JSON key in receipt: {key}"
            )
        result[key] = value
    return result


def load_receipt(path: Path) -> dict[str, object]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise DatasetReceiptVerificationError(
            "O_NOFOLLOW is required for receipt verification"
        )

    fd = -1
    try:
        fd = os.open(Path(path), os.O_RDONLY | nofollow)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise DatasetReceiptVerificationError(
                "receipt evidence is not a regular file"
            )
        if metadata.st_size > MAX_RECEIPT_BYTES:
            raise DatasetReceiptVerificationError(
                f"receipt evidence exceeds {MAX_RECEIPT_BYTES} bytes"
            )
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1
            raw = handle.read(MAX_RECEIPT_BYTES + 1)
            after = os.fstat(handle.fileno())
            if len(raw) > MAX_RECEIPT_BYTES:
                raise DatasetReceiptVerificationError(
                    f"receipt evidence exceeds {MAX_RECEIPT_BYTES} bytes"
                )
            if (
                len(raw) != metadata.st_size
                or after.st_size != metadata.st_size
                or after.st_mtime_ns != metadata.st_mtime_ns
                or after.st_ctime_ns != metadata.st_ctime_ns
            ):
                raise DatasetReceiptVerificationError(
                    "receipt evidence changed while reading"
                )
    except FileNotFoundError as exc:
        raise DatasetReceiptVerificationError(
            f"receipt evidence does not exist: {path}"
        ) from exc
    except OSError as exc:
        raise DatasetReceiptVerificationError(
            f"receipt evidence is unsafe or unreadable: {path}"
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)

    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DatasetReceiptVerificationError(
            "receipt evidence is not valid UTF-8 JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise DatasetReceiptVerificationError("receipt evidence must be an object")
    return payload


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DatasetReceiptVerificationError(
            f"{field} must be a lowercase 64-character SHA-256"
        )
    return value


def validate_receipt_payload(payload: dict[str, object]) -> dict[str, object]:
    expected_top_keys = {
        "schema",
        "artifact_count",
        "artifacts",
        "receipt_sha256",
    }
    actual_top_keys = set(payload)
    if actual_top_keys not in (expected_top_keys, expected_top_keys | {"ok"}):
        raise DatasetReceiptVerificationError(
            "receipt top-level keys do not match schema"
        )
    if "ok" in payload and payload["ok"] is not True:
        raise DatasetReceiptVerificationError(
            "receipt CLI success marker must be exactly true"
        )

    if payload["schema"] != RECEIPT_SCHEMA:
        raise DatasetReceiptVerificationError("receipt schema mismatch")

    artifact_count = payload["artifact_count"]
    if (
        isinstance(artifact_count, bool)
        or not isinstance(artifact_count, int)
        or artifact_count != len(M0_ARTIFACTS)
    ):
        raise DatasetReceiptVerificationError("receipt artifact_count mismatch")

    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(M0_ARTIFACTS):
        raise DatasetReceiptVerificationError("receipt artifacts length mismatch")

    normalized_rows: list[dict[str, object]] = []
    for expected_name, row in zip(M0_ARTIFACTS, artifacts, strict=True):
        if not isinstance(row, dict):
            raise DatasetReceiptVerificationError(
                f"artifact row is not an object: {expected_name}"
            )
        if set(row) != {"name", "size_bytes", "sha256"}:
            raise DatasetReceiptVerificationError(
                f"artifact row keys do not match schema: {expected_name}"
            )
        if row["name"] != expected_name:
            raise DatasetReceiptVerificationError(
                f"artifact order/name mismatch: expected {expected_name}"
            )
        size_bytes = row["size_bytes"]
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            raise DatasetReceiptVerificationError(
                f"artifact size_bytes is invalid: {expected_name}"
            )
        sha256 = _validate_sha256(
            row["sha256"],
            field=f"{expected_name}.sha256",
        )
        normalized_rows.append(
            {
                "name": expected_name,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )

    receipt_sha256 = _validate_sha256(
        payload["receipt_sha256"],
        field="receipt_sha256",
    )
    core: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "artifact_count": len(M0_ARTIFACTS),
        "artifacts": normalized_rows,
    }
    calculated = hashlib.sha256(
        json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if calculated != receipt_sha256:
        raise DatasetReceiptVerificationError(
            "receipt_sha256 does not match receipt contents"
        )

    return {**core, "receipt_sha256": receipt_sha256}


def verify_dataset_receipt(
    dataset_root: Path,
    receipt_payload: dict[str, object],
) -> dict[str, object]:
    expected = validate_receipt_payload(receipt_payload)
    try:
        current = build_dataset_receipt(dataset_root)
    except DatasetReceiptError as exc:
        raise DatasetReceiptVerificationError(
            f"dataset is unsafe or incomplete: {exc}"
        ) from exc

    if current != expected:
        expected_rows = {
            row["name"]: row for row in expected["artifacts"]  # type: ignore[index]
        }
        current_rows = {
            row["name"]: row for row in current["artifacts"]  # type: ignore[index]
        }
        drift = [
            name
            for name in M0_ARTIFACTS
            if expected_rows[name] != current_rows[name]
        ]
        raise DatasetReceiptVerificationError(
            "dataset receipt mismatch"
            + (f": changed artifacts: {','.join(drift)}" if drift else "")
        )

    return {
        "schema": "haxlab-m0-dataset-receipt-verification-v1",
        "ok": True,
        "receipt_sha256": expected["receipt_sha256"],
        "artifact_count": expected["artifact_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.dataset_receipt_verify"
    )
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()

    try:
        report = verify_dataset_receipt(
            args.dataset_root,
            load_receipt(args.receipt),
        )
    except DatasetReceiptVerificationError as exc:
        print(
            json.dumps(
                {
                    "schema": "haxlab-m0-dataset-receipt-verification-v1",
                    "ok": False,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
