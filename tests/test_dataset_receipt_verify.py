from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS, build_dataset_receipt
from haxlab.ingestion.dataset_receipt_verify import (
    DatasetReceiptVerificationError,
    load_receipt,
    validate_receipt_payload,
    verify_dataset_receipt,
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


def _write_receipt(
    path: Path,
    payload: dict[str, object],
    *,
    cli_shape: bool = False,
) -> None:
    if cli_shape:
        payload = {**payload, "ok": True}
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("cli_shape", [False, True])
def test_verify_accepts_api_and_cli_receipt_shapes(
    tmp_path: Path,
    cli_shape: bool,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(receipt_path, receipt, cli_shape=cli_shape)

    loaded = load_receipt(receipt_path)
    report = verify_dataset_receipt(root, loaded)

    assert report == {
        "schema": "haxlab-m0-dataset-receipt-verification-v1",
        "ok": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "artifact_count": len(M0_ARTIFACTS),
    }


def test_verify_reports_exact_artifact_byte_drift(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)

    with (root / "matches.jsonl").open("ab") as handle:
        handle.write(b'{"match_id":"m-2"}\n')

    with pytest.raises(
        DatasetReceiptVerificationError,
        match=r"dataset receipt mismatch: changed artifacts: matches\.jsonl",
    ):
        verify_dataset_receipt(root, receipt)


def test_verify_wraps_unsafe_or_incomplete_dataset_errors(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)
    (root / "reports.json").unlink()

    with pytest.raises(
        DatasetReceiptVerificationError,
        match=r"dataset is unsafe or incomplete: missing required artifact: reports\.json",
    ):
        verify_dataset_receipt(root, receipt)


def test_validate_rejects_tampered_receipt_body(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)
    tampered = json.loads(json.dumps(receipt))
    tampered["artifacts"][0]["size_bytes"] += 1

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="receipt_sha256 does not match receipt contents",
    ):
        validate_receipt_payload(tampered)


def test_validate_rejects_noncanonical_artifact_types(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)
    malformed = json.loads(json.dumps(receipt))
    malformed["artifacts"][0]["size_bytes"] = True

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="artifact size_bytes is invalid: manifest.json",
    ):
        validate_receipt_payload(malformed)


def test_validate_rejects_artifact_order_or_name_drift(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)
    malformed = json.loads(json.dumps(receipt))
    malformed["artifacts"][0], malformed["artifacts"][1] = (
        malformed["artifacts"][1],
        malformed["artifacts"][0],
    )

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="artifact order/name mismatch: expected manifest.json",
    ):
        validate_receipt_payload(malformed)


def test_validate_rejects_unknown_top_level_keys_and_false_cli_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    receipt = build_dataset_receipt(root)

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="receipt top-level keys do not match schema",
    ):
        validate_receipt_payload({**receipt, "unexpected": "value"})

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="receipt CLI success marker must be exactly true",
    ):
        validate_receipt_payload({**receipt, "ok": False})


def test_load_receipt_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        '{"schema":"one","schema":"two"}',
        encoding="utf-8",
    )

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="duplicate JSON key in receipt: schema",
    ):
        load_receipt(receipt_path)


def test_load_receipt_rejects_symlink_evidence(tmp_path: Path) -> None:
    target = tmp_path / "actual.json"
    target.write_text("{}\n", encoding="utf-8")
    linked = tmp_path / "receipt.json"
    linked.symlink_to(target)

    with pytest.raises(
        DatasetReceiptVerificationError,
        match="receipt evidence is unsafe or unreadable",
    ):
        load_receipt(linked)
