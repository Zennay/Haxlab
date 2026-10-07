from __future__ import annotations

import hashlib
import os
import struct
import zlib
from pathlib import Path

import haxlab.runtime.worker as worker_module
from haxlab.runtime.state import RuntimeState
from haxlab.runtime.worker import process_batch


def _valid_hbr2(*, total_frames: int = 3600, payload: bytes = b"payload-data") -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + compressed


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _processing_row(state: RuntimeState, sha256: str):
    return state.connection.execute(
        """
        SELECT status, format_version, total_frames, decompressed_bytes, error
        FROM replay_processing
        WHERE sha256 = ?
        """,
        (sha256,),
    ).fetchone()


def test_worker_rejects_archive_hash_drift_before_probe(tmp_path: Path) -> None:
    original = _valid_hbr2(total_frames=3600, payload=b"A" * 64)
    replacement = _valid_hbr2(total_frames=7200, payload=b"B" * 64)
    archive = tmp_path / "raw" / "replay.hbr2"
    archive.parent.mkdir()
    archive.write_bytes(original)

    digest = _sha(original)
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(archive),
            size_bytes=len(original),
        )
        archive.write_bytes(replacement)

        result = process_batch(state, batch_size=1)
        row = _processing_row(state, digest)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["format_version"] is None
    assert row["total_frames"] is None
    assert row["decompressed_bytes"] is None
    assert str(row["error"]).startswith("archive_sha256_mismatch:")


def test_worker_rejects_symlinked_archive_even_when_target_matches(
    tmp_path: Path,
) -> None:
    payload = _valid_hbr2()
    target = tmp_path / "target.hbr2"
    target.write_bytes(payload)
    archive = tmp_path / "raw" / "replay.hbr2"
    archive.parent.mkdir()
    archive.symlink_to(target)

    digest = _sha(payload)
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(archive),
            size_bytes=len(payload),
        )

        result = process_batch(state, batch_size=1)
        row = _processing_row(state, digest)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["error"] == "archive_symlink_not_allowed"


def test_worker_rejects_regular_file_replacement_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original = _valid_hbr2(payload=b"stable")
    replacement_bytes = _valid_hbr2(payload=b"replacement")
    archive = tmp_path / "raw" / "replay.hbr2"
    archive.parent.mkdir()
    archive.write_bytes(original)
    replacement = tmp_path / "replacement.hbr2"
    replacement.write_bytes(replacement_bytes)

    digest = _sha(original)
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(archive),
            size_bytes=len(original),
        )

        real_open = os.open
        swapped = False

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(path) == archive:
                swapped = True
                replacement.replace(archive)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(worker_module.os, "open", swapping_open)
        result = process_batch(state, batch_size=1)
        row = _processing_row(state, digest)

    assert swapped is True
    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["error"] == "archive_identity_changed"


def test_worker_uses_one_verified_descriptor_snapshot_for_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = _valid_hbr2(total_frames=4200, payload=b"verified")
    archive = tmp_path / "raw" / "replay.hbr2"
    archive.parent.mkdir()
    archive.write_bytes(payload)
    digest = _sha(payload)

    read_calls = 0
    real_read = os.read

    def counted_read(fd, count):
        nonlocal read_calls
        read_calls += 1
        return real_read(fd, count)

    monkeypatch.setattr(worker_module.os, "read", counted_read)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(archive),
            size_bytes=len(payload),
        )
        result = process_batch(state, batch_size=1)
        row = _processing_row(state, digest)

    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert row["status"] == "ok"
    assert row["format_version"] == 3
    assert row["total_frames"] == 4200
    assert row["decompressed_bytes"] == len(b"verified")
    assert row["error"] is None
    assert read_calls >= 2
