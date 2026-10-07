from __future__ import annotations

import hashlib
import os
import struct
import zlib
from pathlib import Path

import haxlab.runtime.archive as archive_module
from haxlab.runtime.archive import archive_path_for, archive_replay
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



def test_duplicate_rejects_destination_replacement_during_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()
    payload = _valid_hbr2(payload=b"duplicate-destination")
    first = incoming / "first.hbr2"
    second = incoming / "second.hbr2"
    replacement = tmp_path / "replacement.hbr2"
    first.write_bytes(payload)
    second.write_bytes(payload)
    replacement.write_bytes(payload)

    with RuntimeState(db) as state:
        first_result = archive_replay(first, raw, state)
        destination = first_result.archive_path
        real_open = os.open
        swapped = False

        def swapping_open(target, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(target) == destination:
                swapped = True
                replacement.replace(destination)
            return real_open(target, flags, *args, **kwargs)

        monkeypatch.setattr(archive_module.os, "open", swapping_open)
        try:
            archive_replay(second, raw, state)
        except RuntimeError as exc:
            assert str(exc) == f"raw_archive_hash_mismatch:{first_result.sha256}"
        else:
            raise AssertionError("destination identity replacement must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert swapped is True
    assert destination.read_bytes() == payload
    assert count == 1


def test_recovery_rejects_symlinked_existing_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    external = tmp_path / "external.hbr2"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"recovery-symlink")
    source.write_bytes(payload)
    external.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()
    destination = archive_path_for(raw, digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(external)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == f"raw_archive_hash_mismatch:{digest}"
        else:
            raise AssertionError("symlinked recovery destination must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert destination.is_symlink()
    assert external.read_bytes() == payload
    assert count == 0


def test_concurrent_invalid_winner_is_never_clobbered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"publisher")
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    destination = archive_path_for(raw, digest)
    competitor = b"competitor-corrupt-object"

    raced = False

    def racing_link(src, dst, *args, **kwargs):
        nonlocal raced
        raced = True
        Path(dst).write_bytes(competitor)
        raise FileExistsError(str(dst))

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "link", racing_link)
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == f"raw_archive_hash_mismatch:{digest}"
        else:
            raise AssertionError("invalid concurrent winner must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert raced is True
    assert destination.read_bytes() == competitor
    assert count == 0


def test_concurrent_valid_winner_is_verified_and_recovered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    payload = _valid_hbr2(payload=b"publisher")
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    destination = archive_path_for(raw, digest)

    raced = False

    def racing_link(src, dst, *args, **kwargs):
        nonlocal raced
        raced = True
        Path(dst).write_bytes(Path(src).read_bytes())
        raise FileExistsError(str(dst))

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "link", racing_link)
        result = archive_replay(source, raw, state)
        row = state.connection.execute(
            "SELECT sha256, archive_path, size_bytes FROM raw_replays"
        ).fetchone()

    assert raced is True
    assert result.duplicate is False
    assert result.sha256 == digest
    assert destination.read_bytes() == payload
    assert row is not None
    assert row["sha256"] == digest
    assert row["archive_path"] == str(destination)
    assert row["size_bytes"] == len(payload)


def test_archive_replay_rejects_symlinked_raw_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    external = tmp_path / "external"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    external.mkdir()
    source.write_bytes(_valid_hbr2(payload=b"root-symlink"))
    raw.symlink_to(external, target_is_directory=True)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "raw_archive_root_unsafe"
        else:
            raise AssertionError("symlinked raw root must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert count == 0
    assert list(external.iterdir()) == []


def test_archive_replay_rejects_symlinked_raw_root_parent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    external = tmp_path / "external"
    redirected_parent = tmp_path / "redirected-parent"
    raw = redirected_parent / "raw"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    external.mkdir()
    source.write_bytes(_valid_hbr2(payload=b"parent-symlink"))
    redirected_parent.symlink_to(external, target_is_directory=True)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "raw_archive_root_unsafe"
        else:
            raise AssertionError("symlinked raw-root parent must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert count == 0
    assert list(external.iterdir()) == []


def test_archive_replay_rejects_symlinked_staging_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    external = tmp_path / "external"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    raw.mkdir()
    external.mkdir()
    source.write_bytes(_valid_hbr2(payload=b"staging-symlink"))
    (raw / ".staging").symlink_to(external, target_is_directory=True)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "raw_archive_directory_unsafe:.staging"
        else:
            raise AssertionError("symlinked staging directory must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert count == 0
    assert list(external.iterdir()) == []


def test_archive_replay_rejects_symlinked_first_hash_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    external = tmp_path / "external"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    raw.mkdir()
    external.mkdir()
    payload = _valid_hbr2(payload=b"first-prefix-symlink")
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (raw / digest[:2]).symlink_to(external, target_is_directory=True)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == f"raw_archive_directory_unsafe:{digest[:2]}"
        else:
            raise AssertionError("symlinked first hash directory must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert count == 0
    assert list(external.iterdir()) == []
    assert not list((raw / ".staging").glob("*"))


def test_archive_replay_rejects_symlinked_second_hash_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    external = tmp_path / "external"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    raw.mkdir()
    external.mkdir()
    payload = _valid_hbr2(payload=b"second-prefix-symlink")
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    first = raw / digest[:2]
    first.mkdir()
    (first / digest[2:4]).symlink_to(external, target_is_directory=True)

    with RuntimeState(db) as state:
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == f"raw_archive_directory_unsafe:{digest[2:4]}"
        else:
            raise AssertionError("symlinked second hash directory must fail closed")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert count == 0
    assert list(external.iterdir()) == []
    assert not list((raw / ".staging").glob("*"))


def test_archive_replay_rejects_raw_root_swap_before_ledger_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "incoming" / "source.hbr2"
    raw = tmp_path / "raw"
    moved_raw = tmp_path / "raw-original"
    redirected = tmp_path / "redirected"
    db = tmp_path / "state.sqlite3"
    source.parent.mkdir()
    redirected.mkdir()
    payload = _valid_hbr2(payload=b"root-swap")
    source.write_bytes(payload)

    real_link = os.link
    swapped = False

    def swapping_link(src, dst, *args, **kwargs):
        nonlocal swapped
        result = real_link(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            raw.rename(moved_raw)
            raw.symlink_to(redirected, target_is_directory=True)
        return result

    with RuntimeState(db) as state:
        monkeypatch.setattr(archive_module.os, "link", swapping_link)
        try:
            archive_replay(source, raw, state)
        except RuntimeError as exc:
            assert str(exc) == "raw_archive_root_unsafe"
        else:
            raise AssertionError("raw-root replacement must fail before ledger commit")
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]

    assert swapped is True
    assert count == 0
    assert raw.is_symlink()
    assert list(redirected.iterdir()) == []
    digest = hashlib.sha256(payload).hexdigest()
    assert archive_path_for(moved_raw, digest).read_bytes() == payload
    assert not list((moved_raw / ".staging").glob("*"))
