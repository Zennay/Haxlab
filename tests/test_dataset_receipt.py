from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from haxlab.ingestion import dataset_receipt as receipt_module

from haxlab.ingestion.dataset_receipt import (
    M0_ARTIFACTS,
    RECEIPT_SCHEMA,
    DatasetReceiptError,
    build_dataset_receipt,
)


def _write_dataset(root: Path, *, reverse: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": b'{"match_count":1}\n',
        "replays.json": b'[{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]\n',
        "duplicates.json": b'[]\n',
        "reports.json": b'[]\n',
        "matches.jsonl": b'{"match_id":"m-1"}\n',
    }
    names = list(M0_ARTIFACTS)
    if reverse:
        names.reverse()
    for name in names:
        (root / name).write_bytes(payloads[name])


def test_receipt_binds_exact_bytes_and_is_deterministic(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_dataset(first_root)
    _write_dataset(second_root, reverse=True)

    first = build_dataset_receipt(first_root)
    second = build_dataset_receipt(second_root)

    assert first == second
    assert first["schema"] == RECEIPT_SCHEMA
    assert first["artifact_count"] == len(M0_ARTIFACTS)
    assert [row["name"] for row in first["artifacts"]] == list(M0_ARTIFACTS)

    core = {
        "schema": first["schema"],
        "artifact_count": first["artifact_count"],
        "artifacts": first["artifacts"],
    }
    expected = hashlib.sha256(
        json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert first["receipt_sha256"] == expected


def test_receipt_changes_when_any_artifact_byte_changes(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    before = build_dataset_receipt(root)

    with (root / "matches.jsonl").open("ab") as handle:
        handle.write(b'{"match_id":"m-2"}\n')

    after = build_dataset_receipt(root)

    assert before["receipt_sha256"] != after["receipt_sha256"]
    before_rows = {row["name"]: row for row in before["artifacts"]}
    after_rows = {row["name"]: row for row in after["artifacts"]}
    assert (
        before_rows["matches.jsonl"]["sha256"]
        != after_rows["matches.jsonl"]["sha256"]
    )
    for name in set(M0_ARTIFACTS) - {"matches.jsonl"}:
        assert before_rows[name] == after_rows[name]


def test_receipt_rejects_missing_artifact(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    (root / "reports.json").unlink()

    with pytest.raises(DatasetReceiptError, match="missing required artifact: reports.json"):
        build_dataset_receipt(root)


def test_receipt_rejects_symlink_artifact(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    target = tmp_path / "outside-reports.json"
    target.write_text("[]\n", encoding="utf-8")
    (root / "reports.json").unlink()
    (root / "reports.json").symlink_to(target)

    with pytest.raises(
        DatasetReceiptError,
        match="unsafe or unreadable artifact: reports.json",
    ):
        build_dataset_receipt(root)


def test_receipt_rejects_non_regular_artifact(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    (root / "duplicates.json").unlink()
    (root / "duplicates.json").mkdir()

    with pytest.raises(
        DatasetReceiptError,
        match="artifact is not a regular file: duplicates.json",
    ):
        build_dataset_receipt(root)


def test_receipt_rejects_symlink_dataset_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    _write_dataset(real_root)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(
        DatasetReceiptError,
        match="dataset root is unsafe or unreadable",
    ):
        build_dataset_receipt(linked_root)



def test_receipt_rejects_artifact_changed_while_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    real_fstat = receipt_module.os.fstat
    seen_by_fd: dict[int, int] = {}

    def drifting_fstat(fd: int) -> object:
        current = real_fstat(fd)
        seen_by_fd[fd] = seen_by_fd.get(fd, 0) + 1
        if seen_by_fd[fd] == 3:
            return SimpleNamespace(
                st_mode=current.st_mode,
                st_dev=current.st_dev,
                st_ino=current.st_ino,
                st_size=current.st_size,
                st_mtime_ns=current.st_mtime_ns + 1,
                st_ctime_ns=current.st_ctime_ns,
            )
        return current

    monkeypatch.setattr(receipt_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        DatasetReceiptError,
        match="artifact changed while hashing: manifest.json",
    ):
        build_dataset_receipt(root)


def test_receipt_rejects_dataset_root_changed_during_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    _write_dataset(root)
    real_fstat = receipt_module.os.fstat
    root_fd: int | None = None
    root_reads = 0

    def drifting_root_fstat(fd: int) -> object:
        nonlocal root_fd, root_reads
        current = real_fstat(fd)
        if root_fd is None:
            root_fd = fd
        if fd == root_fd:
            root_reads += 1
            if root_reads == 2:
                return SimpleNamespace(
                    st_mode=current.st_mode,
                    st_dev=current.st_dev,
                    st_ino=current.st_ino,
                    st_size=current.st_size,
                    st_mtime_ns=current.st_mtime_ns + 1,
                    st_ctime_ns=current.st_ctime_ns,
                )
        return current

    monkeypatch.setattr(receipt_module.os, "fstat", drifting_root_fstat)

    with pytest.raises(
        DatasetReceiptError,
        match="dataset root changed while building receipt",
    ):
        build_dataset_receipt(root)
