from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS
from haxlab.ingestion.dataset_receipt_store import (
    DatasetReceiptStoreError,
    receipt_file_bytes,
    store_dataset_receipt,
)
from haxlab.ingestion.dataset_receipt_verify import (
    load_receipt,
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


def test_store_publishes_verifiable_cli_receipt_atomically(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    output = tmp_path / "receipt.json"

    receipt = store_dataset_receipt(root, output)

    assert output.read_bytes() == receipt_file_bytes(receipt)
    loaded = load_receipt(output)
    report = verify_dataset_receipt(root, loaded)
    assert report["ok"] is True
    assert report["receipt_sha256"] == receipt["receipt_sha256"]


def test_store_rerun_is_byte_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    output = tmp_path / "receipt.json"

    first = store_dataset_receipt(root, output)
    first_bytes = output.read_bytes()
    second = store_dataset_receipt(root, output)

    assert first == second
    assert output.read_bytes() == first_bytes


def test_store_replace_failure_preserves_previous_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    output = tmp_path / "receipt.json"
    previous = b'{"previous":true}\n'
    output.write_bytes(previous)

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "haxlab.ingestion.dataset_receipt_store.os.replace",
        fail_replace,
    )

    with pytest.raises(
        DatasetReceiptStoreError,
        match="receipt publication failed",
    ):
        store_dataset_receipt(root, output)

    assert output.read_bytes() == previous
    assert list(tmp_path.glob(".receipt.json.*.tmp")) == []


def test_store_replaces_symlink_itself_without_touching_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside\n")
    output = tmp_path / "receipt.json"
    output.symlink_to(outside)

    store_dataset_receipt(root, output)

    assert outside.read_bytes() == b"outside\n"
    assert output.is_symlink() is False
    assert load_receipt(output)["ok"] is True


def test_store_rejects_symlink_output_parent(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(
        DatasetReceiptStoreError,
        match="receipt output parent is unsafe or unreadable",
    ):
        store_dataset_receipt(root, linked_parent / "receipt.json")

    assert list(real_parent.iterdir()) == []


def test_store_invalid_dataset_does_not_touch_existing_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    (root / "matches.jsonl").unlink()
    output = tmp_path / "receipt.json"
    previous = b'{"previous":true}\n'
    output.write_bytes(previous)

    with pytest.raises(ValueError, match="missing required artifact"):
        store_dataset_receipt(root, output)

    assert output.read_bytes() == previous
