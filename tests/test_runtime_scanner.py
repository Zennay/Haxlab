import hashlib
import math
import os
import struct
import time
import zlib
from pathlib import Path

import pytest

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

    assert summary.discovered == 1
    assert summary.disappeared == 1
    assert summary.failed == 0
    assert summary.archived == 0
    assert event is not None
    assert event["event_type"] == "replay_disappeared"
    assert event["detail"] == "disappeared_before_stat"


def test_scanner_ignores_symlinked_replay_source(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    outside = tmp_path / "outside.hbr2"
    payload = _valid_hbr2(total_frames=900, payload=b"outside-source")
    outside.write_bytes(payload)
    link = incoming / "linked.hbr2"
    link.symlink_to(outside)

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        source_count = state.connection.execute(
            "SELECT COUNT(*) FROM source_files"
        ).fetchone()[0]
        event = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert summary.discovered == 0
    assert summary.archived == 0
    assert summary.duplicates == 0
    assert summary.failed == 0
    assert snapshot["raw_unique_replays"] == 0
    assert source_count == 0
    assert not list(raw.rglob("*.hbr2"))


def test_scanner_rechecks_symlink_status_before_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"
    incoming.mkdir()

    outside = tmp_path / "outside.hbr2"
    outside.write_bytes(_valid_hbr2(total_frames=901, payload=b"outside-race"))
    replay = incoming / "queued.hbr2"
    replay.write_bytes(_valid_hbr2(total_frames=600, payload=b"initial"))

    original_is_symlink = Path.is_symlink
    checks = 0

    def becomes_symlink_after_discovery(self: Path) -> bool:
        nonlocal checks
        if self == replay:
            checks += 1
            if checks == 1:
                return False
            if checks == 2:
                replay.unlink()
                replay.symlink_to(outside)
                return True
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", becomes_symlink_after_discovery)

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        event = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert checks >= 2
    assert summary.discovered == 0
    assert summary.archived == 0
    assert summary.failed == 0
    assert snapshot["raw_unique_replays"] == 0
    assert not list(raw.rglob("*.hbr2"))


def test_scanner_rejects_symlinked_incoming_root(tmp_path: Path) -> None:
    real_incoming = tmp_path / "real-incoming"
    real_incoming.mkdir()
    (real_incoming / "outside.hbr2").write_bytes(
        _valid_hbr2(total_frames=902, payload=b"root-symlink")
    )
    incoming = tmp_path / "incoming"
    incoming.symlink_to(real_incoming, target_is_directory=True)

    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        with pytest.raises(
            ValueError,
            match="incoming_root_must_not_be_symlink",
        ):
            scan_once(
                incoming,
                raw,
                state,
                minimum_file_age_seconds=0,
                now=time.time() + 10,
            )
        snapshot = state.status_snapshot()
        event = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert snapshot["raw_unique_replays"] == 0
    assert not list(raw.rglob("*.hbr2"))


@pytest.mark.parametrize(
    "minimum_file_age_seconds",
    [math.nan, math.inf, -math.inf, -1.0, True, "30"],
)
def test_scanner_rejects_invalid_minimum_file_age(
    tmp_path: Path,
    minimum_file_age_seconds: object,
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        with pytest.raises(
            ValueError,
            match="minimum_file_age_seconds_must_be_finite_and_nonnegative",
        ):
            scan_once(
                incoming,
                raw,
                state,
                minimum_file_age_seconds=minimum_file_age_seconds,  # type: ignore[arg-type]
                now=time.time(),
            )

    assert not list(raw.rglob("*.hbr2"))


@pytest.mark.parametrize("now", [math.nan, math.inf, -math.inf, True, "0"])
def test_scanner_rejects_invalid_now(
    tmp_path: Path,
    now: object,
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        with pytest.raises(ValueError, match="now_must_be_finite"):
            scan_once(
                incoming,
                raw,
                state,
                minimum_file_age_seconds=0,
                now=now,  # type: ignore[arg-type]
            )

    assert not list(raw.rglob("*.hbr2"))


def test_scanner_rejects_symlinked_nested_directory(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.hbr2").write_bytes(
        _valid_hbr2(total_frames=903, payload=b"nested-directory-link")
    )
    linked_directory = incoming / "linked-dir"
    linked_directory.symlink_to(outside, target_is_directory=True)

    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()
        source_count = state.connection.execute(
            "SELECT COUNT(*) FROM source_files"
        ).fetchone()[0]
        event = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert summary.discovered == 0
    assert summary.archived == 0
    assert summary.failed == 0
    assert snapshot["raw_unique_replays"] == 0
    assert source_count == 0
    assert not list(raw.rglob("*.hbr2"))


def test_scanner_keeps_regular_nested_directories(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    nested = incoming / "nested" / "deep"
    nested.mkdir(parents=True)
    replay = nested / "regular.hbr2"
    replay.write_bytes(_valid_hbr2(total_frames=904, payload=b"nested-regular"))

    raw = tmp_path / "raw"
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        summary = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=0,
            now=time.time() + 10,
        )
        snapshot = state.status_snapshot()

    assert summary.discovered == 1
    assert summary.archived == 1
    assert summary.failed == 0
    assert snapshot["raw_unique_replays"] == 1
    assert len(list(raw.rglob("*.hbr2"))) == 1
