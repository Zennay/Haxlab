from __future__ import annotations

import hashlib
import os
import struct
import zlib
from pathlib import Path

import haxlab.runtime.archive as archive_module
from haxlab.runtime.archive import archive_replay
from haxlab.runtime.state import RuntimeState


def _valid_hbr2(total_frames: int = 600, payload: bytes = b"payload") -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + compressed


def test_archive_replay_binds_healthy_source_bytes_to_ledger(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "healthy.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"healthy")
    source.write_bytes(payload)

    with RuntimeState(db) as state:
        result = archive_replay(source, raw, state)
        row = state.connection.execute(
            "SELECT sha256, archive_path, size_bytes FROM raw_replays"
        ).fetchone()

    digest = hashlib.sha256(payload).hexdigest()
    assert result.sha256 == digest
    assert result.duplicate is False
    assert result.archive_path.read_bytes() == payload
    assert row is not None
    assert row["sha256"] == digest
    assert row["archive_path"] == str(result.archive_path)
    assert row["size_bytes"] == len(payload)


def test_archive_replay_preserves_duplicate_behavior(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()
    payload = _valid_hbr2(payload=b"same")
    first = incoming / "first.hbr2"
    second = incoming / "second.hbr2"
    first.write_bytes(payload)
    second.write_bytes(payload)

    with RuntimeState(db) as state:
        first_result = archive_replay(first, raw, state)
        second_result = archive_replay(second, raw, state)
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert first_result.duplicate is False
    assert second_result.duplicate is True
    assert second_result.archive_path == first_result.archive_path
    assert count == 1
    assert len(list(raw.rglob("*.hbr2"))) == 1


def test_archive_replay_rejects_regular_file_replacement_before_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    replacement = tmp_path / "replacement.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    source.write_bytes(_valid_hbr2(payload=b"original"))
    replacement.write_bytes(_valid_hbr2(payload=b"replacement"))

    real_open = os.open
    swapped = False

    def swapping_open(target, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(target) == source:
            swapped = True
            replacement.replace(source)
        return real_open(target, flags, *args, **kwargs)

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "open", swapping_open)
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "source_changed_during_archive"
        else:
            raise AssertionError("source replacement must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert swapped is True
    assert count == 0
    assert not list(raw.rglob("*.hbr2"))
    assert not list((raw / ".staging").glob("*"))


def test_archive_replay_rejects_symlink_replacement_before_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    redirected = tmp_path / "redirected.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"source")
    source.write_bytes(payload)
    redirected.write_bytes(payload)

    real_open = os.open
    swapped = False

    def swapping_open(target, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(target) == source:
            swapped = True
            source.unlink()
            source.symlink_to(redirected)
        return real_open(target, flags, *args, **kwargs)

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "open", swapping_open)
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "source_changed_during_archive"
        else:
            raise AssertionError("source symlink replacement must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert swapped is True
    assert source.is_symlink()
    assert count == 0
    assert not list(raw.rglob("*.hbr2"))
    assert not list((raw / ".staging").glob("*"))


def test_archive_replay_rejects_in_place_source_mutation_during_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"A" * 4096)
    source.write_bytes(payload)

    real_read = os.read
    mutated = False

    def mutating_read(fd, count):
        nonlocal mutated
        chunk = real_read(fd, count)
        if not mutated and chunk:
            mutated = True
            changed = bytearray(payload)
            changed[-1] ^= 0x01
            with source.open("r+b") as handle:
                handle.write(changed)
                handle.flush()
                os.fsync(handle.fileno())
        return chunk

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "read", mutating_read)
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "source_changed_during_archive"
        else:
            raise AssertionError("in-place mutation must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert mutated is True
    assert count == 0
    assert not list(raw.rglob("*.hbr2"))
    assert not list((raw / ".staging").glob("*"))
