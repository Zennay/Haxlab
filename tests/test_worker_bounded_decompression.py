from __future__ import annotations

import struct
import zlib

import pytest

import haxlab.runtime.worker as worker_module
from haxlab.replay.header import ReplayFormatError
from haxlab.runtime.worker import _probe_verified_replay


def _hbr2(payload: bytes, *, total_frames: int = 3600) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + compressed


def test_probe_counts_large_payload_through_bounded_output_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_decompressobj = worker_module.zlib.decompressobj
    max_lengths: list[int] = []

    class RecordingDecompressor:
        def __init__(self, *args, **kwargs) -> None:
            self._inner = real_decompressobj(*args, **kwargs)

        @property
        def eof(self) -> bool:
            return self._inner.eof

        @property
        def unconsumed_tail(self) -> bytes:
            return self._inner.unconsumed_tail

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            max_lengths.append(max_length)
            return self._inner.decompress(data, max_length)

    monkeypatch.setattr(
        worker_module.zlib,
        "decompressobj",
        RecordingDecompressor,
    )

    payload = b"A" * (worker_module._DECOMPRESS_CHUNK_BYTES * 3 + 123)
    header, decompressed_bytes = _probe_verified_replay(_hbr2(payload))

    assert header.version == 3
    assert header.total_frames == 3600
    assert decompressed_bytes == len(payload)
    assert len(max_lengths) >= 4
    assert set(max_lengths) == {worker_module._DECOMPRESS_CHUNK_BYTES}


def test_probe_returns_size_instead_of_retaining_decompressed_payload() -> None:
    payload = (b"bounded-worker-memory-" * 10000) + b"tail"

    _, decompressed_bytes = _probe_verified_replay(_hbr2(payload))

    assert type(decompressed_bytes) is int
    assert decompressed_bytes == len(payload)


def test_probe_preserves_valid_empty_payload_semantics() -> None:
    header, decompressed_bytes = _probe_verified_replay(_hbr2(b"", total_frames=0))

    assert header.total_frames == 0
    assert decompressed_bytes == 0


def test_probe_rejects_truncated_deflate_stream() -> None:
    replay = _hbr2(b"A" * 8192)

    with pytest.raises(ReplayFormatError, match=r"^deflate_error:"):
        _probe_verified_replay(replay[:-1])
