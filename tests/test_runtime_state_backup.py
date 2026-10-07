from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

import haxlab.runtime.state_backup as state_backup
from haxlab.runtime.state_backup import (
    RECEIPT_SCHEMA,
    BackupReceipt,
    StateBackupError,
    backup_runtime_state,
    main,
    parse_backup_receipt,
    verify_runtime_state_backup,
)


def _create_wal_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence(value) VALUES ('base')")
    connection.commit()
    return connection


def test_backup_includes_committed_wal_data(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    try:
        writer.execute("INSERT INTO evidence(value) VALUES ('wal-commit')")
        writer.commit()

        receipt = backup_runtime_state(source, destination)
    finally:
        writer.close()

    assert not Path(f"{destination}-journal").exists()
    assert not Path(f"{destination}-wal").exists()
    assert not Path(f"{destination}-shm").exists()

    with sqlite3.connect(destination) as snapshot:
        values = [
            row[0]
            for row in snapshot.execute("SELECT value FROM evidence ORDER BY id")
        ]
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)

    assert values == ["base", "wal-commit"]
    assert receipt.schema == RECEIPT_SCHEMA
    assert receipt.size_bytes == destination.stat().st_size
    assert len(receipt.sha256) == 64
    assert receipt.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert receipt.page_count >= 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o400


def test_backup_excludes_uncommitted_wal_data(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    try:
        writer.execute("INSERT INTO evidence(value) VALUES ('uncommitted')")

        backup_runtime_state(source, destination)
        writer.rollback()
    finally:
        writer.close()

    with sqlite3.connect(destination) as snapshot:
        values = [
            row[0]
            for row in snapshot.execute("SELECT value FROM evidence ORDER BY id")
        ]

    assert values == ["base"]


def test_backup_timeout_fails_closed_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    ticks = iter((100.0, 131.0))
    monkeypatch.setattr(
        state_backup,
        "_monotonic",
        lambda: next(ticks, 131.0),
    )

    with pytest.raises(StateBackupError, match="exceeded 30s deadline"):
        backup_runtime_state(source, destination, max_seconds=30.0)

    assert not destination.exists()
    assert list(tmp_path.glob(".haxlab-state-backup-*")) == []


def test_backup_timeout_parameter_is_strict(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    invalid_values: tuple[object, ...] = (
        0,
        -1,
        float("inf"),
        float("-inf"),
        float("nan"),
        True,
        "30",
        10**10000,
    )
    for index, invalid in enumerate(invalid_values):
        destination = tmp_path / f"backup-{index}.sqlite"
        with pytest.raises(
            StateBackupError,
            match="positive finite native number",
        ):
            backup_runtime_state(
                source,
                destination,
                max_seconds=invalid,  # type: ignore[arg-type]
            )
        assert not destination.exists()


def test_backup_never_overwrites_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    destination.write_bytes(b"sentinel")

    with pytest.raises(StateBackupError, match="destination already exists"):
        backup_runtime_state(source, destination)

    assert destination.read_bytes() == b"sentinel"


def test_backup_fails_closed_when_destination_appears_during_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    def racing_link(src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                    dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                    *args: object, **kwargs: object) -> None:
        del src, args, kwargs
        Path(dst).write_bytes(b"racer")
        raise FileExistsError

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(StateBackupError, match="appeared during backup"):
        backup_runtime_state(source, destination)

    assert destination.read_bytes() == b"racer"
    assert list(tmp_path.glob(".haxlab-state-backup-*")) == []


def test_backup_rolls_back_when_directory_durability_cannot_be_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    def fail_directory_fsync(path: Path) -> None:
        del path
        raise StateBackupError("simulated directory fsync failure")

    monkeypatch.setattr(state_backup, "_fsync_directory", fail_directory_fsync)

    with pytest.raises(
        StateBackupError,
        match="simulated directory fsync failure",
    ) as error:
        backup_runtime_state(source, destination)

    assert "rollback failures=" in str(error.value)
    assert "rollback directory durability failed" in str(error.value)
    assert not destination.exists()
    assert list(tmp_path.glob(".haxlab-state-backup-*")) == []


def test_backup_rejects_source_destination_alias(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    with pytest.raises(StateBackupError, match="different paths"):
        backup_runtime_state(source, source)


def test_backup_rejects_symlink_source(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(source)

    with pytest.raises(StateBackupError, match="symlink path component"):
        backup_runtime_state(alias, tmp_path / "backup.sqlite")


def test_backup_rejects_symlink_destination_parent(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(StateBackupError, match="symlink path component"):
        backup_runtime_state(source, alias_parent / "backup.sqlite")


def test_backup_rejects_corrupt_source(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    source.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(StateBackupError, match="quick_check|read-only"):
        backup_runtime_state(source, tmp_path / "backup.sqlite")


def test_parse_backup_receipt_accepts_only_canonical_native_fields() -> None:
    payload = {
        "schema": RECEIPT_SCHEMA,
        "size_bytes": 4096,
        "sha256": "a" * 64,
        "page_count": 1,
    }

    assert parse_backup_receipt(payload) == BackupReceipt(
        schema=RECEIPT_SCHEMA,
        size_bytes=4096,
        sha256="a" * 64,
        page_count=1,
    )

    for field, invalid_value in (
        ("size_bytes", "4096"),
        ("size_bytes", True),
        ("page_count", 1.0),
        ("page_count", False),
        ("sha256", "A" * 64),
    ):
        malformed = dict(payload)
        malformed[field] = invalid_value
        with pytest.raises(StateBackupError):
            parse_backup_receipt(malformed)

    with pytest.raises(StateBackupError, match="fields are not canonical"):
        parse_backup_receipt({**payload, "unexpected": "value"})
    missing = dict(payload)
    del missing["sha256"]
    with pytest.raises(StateBackupError, match="fields are not canonical"):
        parse_backup_receipt(missing)
    with pytest.raises(StateBackupError, match="JSON object"):
        parse_backup_receipt([payload])


def test_verify_runtime_state_backup_accepts_exact_published_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    receipt = backup_runtime_state(source, destination)

    assert verify_runtime_state_backup(destination, receipt) == receipt
    assert not Path(f"{destination}-journal").exists()
    assert not Path(f"{destination}-wal").exists()
    assert not Path(f"{destination}-shm").exists()


def test_snapshot_hash_rejects_symlink_alias(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.sqlite"
    alias = tmp_path / "snapshot-alias.sqlite"
    snapshot.write_bytes(b"not-empty")
    alias.symlink_to(snapshot)

    with pytest.raises(StateBackupError, match="open snapshot for hashing"):
        state_backup._hash_file(alias)


def test_verify_runtime_state_backup_rejects_writable_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    receipt = backup_runtime_state(source, destination)

    destination.chmod(0o600)

    with pytest.raises(StateBackupError, match="sealed read-only"):
        verify_runtime_state_backup(destination, receipt)


def test_verify_runtime_state_backup_rejects_byte_tamper(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    receipt = backup_runtime_state(source, destination)

    original = destination.read_bytes()
    destination.chmod(0o600)
    destination.write_bytes(original + b"tamper")
    destination.chmod(0o400)

    with pytest.raises(StateBackupError, match="size does not match receipt"):
        verify_runtime_state_backup(destination, receipt)


def test_verify_runtime_state_backup_rejects_malformed_receipt(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    receipt = backup_runtime_state(source, destination)
    malformed = BackupReceipt(
        schema=receipt.schema,
        size_bytes=receipt.size_bytes,
        sha256="A" * 64,
        page_count=receipt.page_count,
    )

    with pytest.raises(StateBackupError, match="lowercase 64-char hex"):
        verify_runtime_state_backup(destination, malformed)


def test_verify_runtime_state_backup_rejects_symlink_alias(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    alias = tmp_path / "backup-alias.sqlite"
    writer = _create_wal_database(source)
    writer.close()
    receipt = backup_runtime_state(source, destination)
    alias.symlink_to(destination)

    with pytest.raises(StateBackupError, match="symlink path component"):
        verify_runtime_state_backup(alias, receipt)


def test_cli_emits_machine_readable_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    assert main([str(source), str(destination)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["receipt"]["schema"] == RECEIPT_SCHEMA
    assert payload["receipt"]["size_bytes"] == destination.stat().st_size
    assert len(payload["receipt"]["sha256"]) == 64


def test_cli_file_fsync_failure_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    def fail_fsync(fd: int) -> None:
        del fd
        raise PermissionError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    assert main([str(source), str(destination)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "unable to fsync snapshot file" in payload["error"]
    assert "simulated fsync failure" in payload["error"]
    assert not destination.exists()
    assert list(tmp_path.glob(".haxlab-state-backup-*")) == []


def test_cli_invalid_timeout_is_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    assert main(
        [str(source), str(destination), "--max-seconds", "0"]
    ) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "positive finite native number" in payload["error"]
    assert not destination.exists()


def test_cli_temp_creation_failure_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    def fail_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        del args, kwargs
        raise PermissionError("simulated temp permission failure")

    monkeypatch.setattr(state_backup.tempfile, "mkstemp", fail_mkstemp)

    assert main([str(source), str(destination)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "unable to create private backup temp file" in payload["error"]
    assert "simulated temp permission failure" in payload["error"]
    assert not destination.exists()


def test_cli_temp_permission_failure_is_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "state.sqlite"
    destination = tmp_path / "backup.sqlite"
    writer = _create_wal_database(source)
    writer.close()

    def fail_chmod(path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                   mode: int) -> None:
        del path, mode
        raise PermissionError("simulated chmod failure")

    monkeypatch.setattr(os, "chmod", fail_chmod)

    assert main([str(source), str(destination)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "unable to set private backup temp permissions" in payload["error"]
    assert "simulated chmod failure" in payload["error"]
    assert not destination.exists()
    assert list(tmp_path.glob(".haxlab-state-backup-*")) == []


def test_cli_failure_is_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "missing.sqlite"
    destination = tmp_path / "backup.sqlite"

    assert main([str(source), str(destination)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "does not exist" in payload["error"]
    assert not destination.exists()
