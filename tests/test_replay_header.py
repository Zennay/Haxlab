import struct
import zlib
from pathlib import Path

from haxlab.replay.header import (
    ReplayFormatError,
    decompress_replay_payload,
    read_replay_header,
)


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def test_reads_v3_header_and_payload(tmp_path: Path) -> None:
    payload = b"canonical-test-payload"
    path = tmp_path / "sample.hbr2"
    path.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 28_557) + _raw_deflate(payload)
    )

    header = read_replay_header(path)

    assert header.version == 3
    assert header.total_frames == 28_557
    assert round(header.duration_seconds, 2) == 475.95
    assert decompress_replay_payload(path) == payload


def test_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "sample.hbr2"
    path.write_bytes(struct.pack(">4sII", b"HBR2", 99, 100) + _raw_deflate(b"x"))

    try:
        read_replay_header(path)
    except ReplayFormatError as exc:
        assert "unsupported_version" in str(exc)
    else:
        raise AssertionError("expected ReplayFormatError")
