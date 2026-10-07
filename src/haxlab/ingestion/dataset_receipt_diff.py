from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS
from haxlab.ingestion.dataset_receipt_verify import (
    DatasetReceiptVerificationError,
    load_receipt,
    validate_receipt_payload,
)


DIFF_SCHEMA = "haxlab-m0-dataset-receipt-diff-v1"


def compare_dataset_receipts(
    before_payload: dict[str, object],
    after_payload: dict[str, object],
) -> dict[str, object]:
    """Compare two structurally valid M0 receipts by canonical artifact identity."""

    before = validate_receipt_payload(before_payload)
    after = validate_receipt_payload(after_payload)

    before_rows = {
        row["name"]: row for row in before["artifacts"]  # type: ignore[index]
    }
    after_rows = {
        row["name"]: row for row in after["artifacts"]  # type: ignore[index]
    }

    changes: list[dict[str, object]] = []
    for name in M0_ARTIFACTS:
        before_row = before_rows[name]
        after_row = after_rows[name]
        if before_row == after_row:
            continue
        changes.append(
            {
                "name": name,
                "before": {
                    "size_bytes": before_row["size_bytes"],
                    "sha256": before_row["sha256"],
                },
                "after": {
                    "size_bytes": after_row["size_bytes"],
                    "sha256": after_row["sha256"],
                },
                "size_changed": (
                    before_row["size_bytes"] != after_row["size_bytes"]
                ),
                "sha256_changed": before_row["sha256"] != after_row["sha256"],
            }
        )

    identical = before["receipt_sha256"] == after["receipt_sha256"]
    if identical != (len(changes) == 0):
        raise DatasetReceiptVerificationError(
            "receipt identity is inconsistent with artifact records"
        )

    return {
        "schema": DIFF_SCHEMA,
        "identical": identical,
        "before_receipt_sha256": before["receipt_sha256"],
        "after_receipt_sha256": after["receipt_sha256"],
        "changed_artifact_count": len(changes),
        "changed_artifacts": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.dataset_receipt_diff"
    )
    parser.add_argument("before_receipt", type=Path)
    parser.add_argument("after_receipt", type=Path)
    args = parser.parse_args()

    try:
        report = compare_dataset_receipts(
            load_receipt(args.before_receipt),
            load_receipt(args.after_receipt),
        )
    except DatasetReceiptVerificationError as exc:
        print(
            json.dumps(
                {
                    "schema": DIFF_SCHEMA,
                    "ok": False,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(json.dumps({**report, "ok": True}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
