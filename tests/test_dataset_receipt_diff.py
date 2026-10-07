from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS, build_dataset_receipt
from haxlab.ingestion.dataset_receipt_diff import (
    DIFF_SCHEMA,
    compare_dataset_receipts,
)
from haxlab.ingestion.dataset_receipt_verify import (
    DatasetReceiptVerificationError,
)


def _write_dataset(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": b'{"match_count":1}\n',
        "replays.json": b'[{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]\n',
        "duplicates.json": b'[]\n',
        "reports.json": b'[]\n',
        "matches.jsonl": b'{"match_id":"m-1"}\n',
    }
    for name in M0_ARTIFACTS:
        (root / name).write_bytes(payloads[name])


def test_receipt_diff_reports_identical_publications(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_dataset(first_root)
    _write_dataset(second_root)

    report = compare_dataset_receipts(
        build_dataset_receipt(first_root),
        build_dataset_receipt(second_root),
    )

    assert report == {
        "schema": DIFF_SCHEMA,
        "identical": True,
        "before_receipt_sha256": report["before_receipt_sha256"],
        "after_receipt_sha256": report["after_receipt_sha256"],
        "changed_artifact_count": 0,
        "changed_artifacts": [],
    }
    assert report["before_receipt_sha256"] == report["after_receipt_sha256"]


def test_receipt_diff_detects_same_size_byte_drift(tmp_path: Path) -> None:
    before_root = tmp_path / "before"
    after_root = tmp_path / "after"
    _write_dataset(before_root)
    _write_dataset(after_root)
    (after_root / "reports.json").write_bytes(b"{}\n")

    report = compare_dataset_receipts(
        build_dataset_receipt(before_root),
        build_dataset_receipt(after_root),
    )

    assert report["identical"] is False
    assert report["changed_artifact_count"] == 1
    assert report["changed_artifacts"] == [
        {
            "name": "reports.json",
            "before": {
                "size_bytes": 3,
                "sha256": build_dataset_receipt(before_root)["artifacts"][3][
                    "sha256"
                ],
            },
            "after": {
                "size_bytes": 3,
                "sha256": build_dataset_receipt(after_root)["artifacts"][3][
                    "sha256"
                ],
            },
            "size_changed": False,
            "sha256_changed": True,
        }
    ]


def test_receipt_diff_keeps_canonical_artifact_order(tmp_path: Path) -> None:
    before_root = tmp_path / "before"
    after_root = tmp_path / "after"
    _write_dataset(before_root)
    _write_dataset(after_root)
    (after_root / "manifest.json").write_bytes(b'{"match_count":2}\n')
    (after_root / "matches.jsonl").write_bytes(b'{"match_id":"m-2"}\n')

    report = compare_dataset_receipts(
        build_dataset_receipt(before_root),
        build_dataset_receipt(after_root),
    )

    assert report["changed_artifact_count"] == 2
    assert [
        row["name"] for row in report["changed_artifacts"]
    ] == ["manifest.json", "matches.jsonl"]


def test_receipt_diff_accepts_cli_success_shape(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)

    report = compare_dataset_receipts(
        {**receipt, "ok": True},
        {**receipt, "ok": True},
    )

    assert report["identical"] is True
    assert report["changed_artifact_count"] == 0


def test_receipt_diff_fails_closed_on_malformed_receipt(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="receipt top-level keys do not match schema",
    ):
        compare_dataset_receipts(
            receipt,
            {**receipt, "unexpected": "field"},
        )
