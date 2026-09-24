import struct
import time
import zlib
from pathlib import Path

from haxlab.runtime.scanner import scan_once
from haxlab.runtime.state import RuntimeState


def _valid_hbr2(total_frames: int = 600) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    payload = compressor.compress(b"payload") + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + payload


def test_scanner_archives_once_and_skips_unchanged(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    db = tmp_path / "state" / "haxlab.sqlite3"
    incoming.mkdir()

    replay = incoming / "one.hbr2"
    replay.write_bytes(_valid_hbr2())

    old = time.time() - 120

    with RuntimeState(db) as state:
        first = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=30,
            now=old + 120,
        )
        second = scan_once(
            incoming,
            raw,
            state,
            minimum_file_age_seconds=30,
            now=old + 121,
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
