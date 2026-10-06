import hashlib
import os
import struct
import time
import zlib
from pathlib import Path

import haxlab.runtime.archive as archive_module
from haxlab.runtime.archive import archive_path_for
from haxlab.runtime.scanner import scan_once
from haxlab.runtime.state import RuntimeState


def _valid_hbr2(total_frames: int = 600, payload: bytes = b"payload") -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + compressed


def test_scanner_archives_once_and_skips_unchanged(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state" / "haxlab.sqlite3"
    incoming.mkdir()

    replay = incoming / "one.hbr2"
    replay.write_bytes(_valid_hbr2())

    old = time.time() - 120
    os.utime(replay, (old, old))

    with RuntimeState(db) as state:
        first = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=30,
            now=time.time(),
        )
        second = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=30,
            now=time.time() + 1,
        )

    assert first.archived == 1
    assert second.unchanged == 1
    assert len(list(raw.rglob("*.hbr2"))) == 1


def test_duplicate_content_is_only_stored_once(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    payload = _valid_hbr2()
    (incoming / "a.hbr2").write_bytes(payload)
    (incoming / "b.hbr2").write_bytes(payload)

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )

    assert summary.archived == 1
    assert summary.duplicates == 1
    assert len(list(raw.rglob("*.hbr2"))) == 1


def test_source_change_during_archive_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    replay = incoming / "changing.hbr2"
    replay.write_bytes(_valid_hbr2(total_frames=600, payload=b"first"))

    original_copy = archive_module._copy_to_staging_and_hash

    def copy_then_mutate(source_path: Path, raw_root: Path):
        result = original_copy(source_path, raw_root)
        source_path.write_bytes(_valid_hbr2(total_frames=601, payload=b"second"))
        return result

    monkeypatch.setattr(
        archive_module,
        "_copy_to_staging_and_hash",
        copy_then_mutate,
    )

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        row = state.connection.execute(
            "SELECT status, error FROM source_files WHERE source_path = ?",
            (str(replay.resolve()),),
        ).fetchone()

    assert summary.failed == 1
    assert summary.archived == 0
    assert summary.duplicates == 0
    assert snapshot["raw_unique_replays"] == 0
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "source_changed_during_archive"
    assert not list(raw.rglob("*.hbr2"))
    assert not list((raw / ".staging").glob("*"))


def test_recovers_finalized_archive_missing_from_ledger(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    payload = _valid_hbr2(total_frames=777, payload=b"crash-recovery")
    replay = incoming / "recover.hbr2"
    replay.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()
    destination = archive_path_for(raw, digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        known = state.get_source(str(replay.resolve()))

    assert summary.archived == 1
    assert summary.duplicates == 0
    assert snapshot["raw_unique_replays"] == 1
    assert known is not None
    assert known.sha256 == digest
    assert known.status == "archived"
    assert destination.read_bytes() == payload
    assert len(list(raw.rglob("*.hbr2"))) == 1


def test_existing_corrupt_object_at_content_address_fails_closed(
    tmp_path: Path,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    payload = _valid_hbr2(total_frames=888, payload=b"expected")
    replay = incoming / "corrupt-existing.hbr2"
    replay.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()
    destination = archive_path_for(raw, digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"not-the-replay")

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        row = state.connection.execute(
            "SELECT status, error FROM source_files WHERE source_path = ?",
            (str(replay.resolve()),),
        ).fetchone()

    assert summary.failed == 1
    assert snapshot["raw_unique_replays"] == 0
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == f"raw_archive_hash_mismatch:{digest}"


def test_disappearing_candidate_does_not_crash_ingest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    replay = incoming / "vanishing.hbr2"
    replay.write_bytes(_valid_hbr2())

    original_is_file = Path.is_file
    original_stat = Path.stat

    def visible_during_enumeration(self: Path) -> bool:
        if self == replay:
            return True
        return original_is_file(self)

    def missing_before_stat(self: Path, *args, **kwargs):
        if self == replay:
            raise FileNotFoundError(str(self))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", visible_during_enumeration)
    monkeypatch.setattr(Path, "stat", missing_before_stat)

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        event = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert summary.discovered == 1
    assert summary.disappeared == 1
    assert summary.failed == 0
    assert summary.archived == 0
    assert event is not None
    assert event["event_type"] == "replay_disappeared"
    assert event["detail"] == "disappeared_before_stat"
