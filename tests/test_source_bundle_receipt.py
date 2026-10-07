from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from haxlab.ingestion import source_bundle_receipt as receipt


def _write_tree(root: Path, *, reverse: bool = False) -> None:
    items = [
        ("nested/match.HBR2", b"replay-bytes"),
        ("discord/export.json", b'{"messages": []}\n'),
        ("notes.txt", b"ignored"),
        ("discord/ignored.JSON", b'{"messages": ["ignored"]}\n'),
    ]
    if reverse:
        items.reverse()
    for relative, payload in items:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_receipt_is_relocation_stable_and_tracks_importer_inputs(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_tree(first)
    _write_tree(second, reverse=True)

    first_receipt = receipt.create_source_bundle_receipt(first)
    second_receipt = receipt.create_source_bundle_receipt(second)

    assert first_receipt == second_receipt
    assert first_receipt["schema"] == receipt.SCHEMA
    assert first_receipt["file_count"] == 2
    assert first_receipt["hbr2_count"] == 1
    assert first_receipt["discord_json_count"] == 1
    assert [item["path"] for item in first_receipt["files"]] == [
        "discord/export.json",
        "nested/match.HBR2",
    ]
    assert {item["kind"] for item in first_receipt["files"]} == {
        "discord_json",
        "hbr2",
    }
    assert len(first_receipt["receipt_sha256"]) == 64


def test_receipt_changes_when_relevant_bytes_change(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    path = root / "match.hbr2"
    path.write_bytes(b"before")
    before = receipt.create_source_bundle_receipt(root)

    path.write_bytes(b"after")
    after = receipt.create_source_bundle_receipt(root)

    assert before["receipt_sha256"] != after["receipt_sha256"]
    assert before["files"][0]["sha256"] != after["files"][0]["sha256"]


def test_receipt_ignores_unrelated_file_changes(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"stable")
    unrelated = root / "notes.txt"
    unrelated.write_text("first", encoding="utf-8")
    before = receipt.create_source_bundle_receipt(root)

    unrelated.write_text("second", encoding="utf-8")
    after = receipt.create_source_bundle_receipt(root)

    assert before == after


@pytest.mark.parametrize("flag_name", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_receipt_fails_closed_without_directory_descriptor_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag_name: str,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"replay")
    monkeypatch.delattr(receipt.os, flag_name, raising=False)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_directory_descriptors_unsupported",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_symlinked_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "match.hbr2").write_bytes(b"replay")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(receipt.SourceBundleReceiptError, match="source_root_symlink"):
        receipt.create_source_bundle_receipt(link)


def test_receipt_rejects_symlinked_parent_component(tmp_path: Path) -> None:
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    root = actual_parent / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"replay")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_root_component_symlink:",
    ):
        receipt.create_source_bundle_receipt(linked_parent / "export")


def test_receipt_rejects_relevant_symlink_file(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    target = tmp_path / "outside.hbr2"
    target.write_bytes(b"replay")
    (root / "match.hbr2").symlink_to(target)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_file_symlink:match.hbr2",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_symlink_directory(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (target / "match.hbr2").write_bytes(b"replay")
    (root / "nested").symlink_to(target, target_is_directory=True)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_directory_symlink:nested",
    ):
        receipt.create_source_bundle_receipt(root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_receipt_rejects_non_regular_relevant_file(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    pipe = root / "stream.hbr2"
    os.mkfifo(pipe)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_file_not_regular:stream.hbr2",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_inventory_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"replay")

    original = receipt._inventory_paths
    calls = 0

    def unstable_inventory(root_fd: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "late.json").write_text("{}", encoding="utf-8")
        return original(root_fd)

    monkeypatch.setattr(receipt, "_inventory_paths", unstable_inventory)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_inventory_changed_during_receipt",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_nested_directory_identity_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "match.hbr2").write_bytes(b"original")

    original = receipt._inventory_paths
    calls = 0

    def swapping_inventory(root_fd: int):
        nonlocal calls
        calls += 1
        result = original(root_fd)
        if calls == 1:
            moved = tmp_path / "old-nested"
            nested.rename(moved)
            nested.mkdir()
            (nested / "match.hbr2").write_bytes(b"replacement")
        return result

    monkeypatch.setattr(receipt, "_inventory_paths", swapping_inventory)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_directory_identity_changed:nested",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_logical_root_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"original")

    original = receipt._inventory_paths
    calls = 0

    def swapping_inventory(root_fd: int):
        nonlocal calls
        calls += 1
        result = original(root_fd)
        if calls == 2:
            moved = tmp_path / "old-export"
            root.rename(moved)
            root.mkdir()
            (root / "match.hbr2").write_bytes(b"replacement")
        return result

    monkeypatch.setattr(receipt, "_inventory_paths", swapping_inventory)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_root_changed_during_receipt",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_same_inode_mutation_after_earlier_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    first = root / "a.hbr2"
    second = root / "z.hbr2"
    first.write_bytes(b"before")
    second.write_bytes(b"second")

    original = receipt._read_regular_file
    calls = 0

    def mutate_after_hash(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            first.write_bytes(b"after!")
        return result

    monkeypatch.setattr(receipt, "_read_regular_file", mutate_after_hash)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_file_changed_after_hash:a.hbr2",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_inode_replacement_after_earlier_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    first = root / "a.hbr2"
    second = root / "z.hbr2"
    first.write_bytes(b"stable")
    second.write_bytes(b"second")

    original = receipt._read_regular_file
    calls = 0

    def replace_after_hash(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            replacement = tmp_path / "replacement.hbr2"
            replacement.write_bytes(b"stable")
            replacement.replace(first)
        return result

    monkeypatch.setattr(receipt, "_read_regular_file", replace_after_hash)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_file_changed_after_hash:a.hbr2",
    ):
        receipt.create_source_bundle_receipt(root)


def test_receipt_rejects_file_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    path = root / "large.hbr2"
    path.write_bytes(b"x" * (1024 * 1024 + 32))

    original_read = receipt.os.read
    mutated = False

    def mutating_read(fd: int, size: int) -> bytes:
        nonlocal mutated
        chunk = original_read(fd, size)
        if chunk and not mutated:
            mutated = True
            with path.open("ab") as handle:
                handle.write(b"changed")
        return chunk

    monkeypatch.setattr(receipt.os, "read", mutating_read)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_file_changed_during_read",
    ):
        receipt.create_source_bundle_receipt(root)


def test_cli_emits_machine_readable_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "match.hbr2").write_bytes(b"replay")

    assert receipt.main([str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == receipt.SCHEMA
    assert payload["file_count"] == 1


def test_cli_emits_machine_readable_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing"

    assert receipt.main([str(missing)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == receipt.SCHEMA
    assert payload["clean"] is False
    assert payload["error"].startswith("source_root_unreadable:")


def test_receipt_fails_closed_on_inventory_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "export"
    root.mkdir()

    def failing_listdir(_fd: int) -> list[str]:
        raise OSError("walk denied")

    monkeypatch.setattr(receipt.os, "listdir", failing_listdir)

    with pytest.raises(
        receipt.SourceBundleReceiptError,
        match="source_inventory_failed:walk denied",
    ):
        receipt.create_source_bundle_receipt(root)
