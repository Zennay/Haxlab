import os
import struct
import zlib
from pathlib import Path

import pytest

from haxlab.replay.header import (
    ReplayFormatError,
    decompress_replay_payload,
    read_replay_header,
)


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def _write_replay(path: Path, payload: bytes = b"payload") -> None:
    path.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 120)
        + _raw_deflate(payload)
    )


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


@pytest.mark.parametrize("reader", [read_replay_header, decompress_replay_payload])
def test_rejects_missing_nofollow_support(
    tmp_path: Path,
    monkeypatch,
    reader,
) -> None:
    path = tmp_path / "sample.hbr2"
    _write_replay(path)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(
        ReplayFormatError,
        match="unsafe_replay_path:nofollow_unsupported",
    ):
        reader(path)


@pytest.mark.parametrize("reader", [read_replay_header, decompress_replay_payload])
def test_rejects_symlink_replay_paths(tmp_path: Path, reader) -> None:
    target = tmp_path / "target.hbr2"
    _write_replay(target)
    link = tmp_path / "redirected.hbr2"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ReplayFormatError, match="unsafe_replay_path:symlink"):
        reader(link)


@pytest.mark.parametrize("reader", [read_replay_header, decompress_replay_payload])
def test_rejects_non_regular_replay_paths(tmp_path: Path, reader) -> None:
    directory = tmp_path / "not-a-replay.hbr2"
    directory.mkdir()

    with pytest.raises(ReplayFormatError, match="unsafe_replay_path:not_regular"):
        reader(directory)


@pytest.mark.parametrize("reader", [read_replay_header, decompress_replay_payload])
def test_rejects_path_replacement_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch,
    reader,
) -> None:
    path = tmp_path / "sample.hbr2"
    replacement = tmp_path / "replacement.hbr2"
    _write_replay(path, b"old")
    _write_replay(replacement, b"new")

    original_open = os.open
    replaced = False

    def replacing_open(candidate, flags):
        nonlocal replaced
        if not replaced and Path(candidate) == path:
            replacement.replace(path)
            replaced = True
        return original_open(candidate, flags)

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(ReplayFormatError, match="unsafe_replay_path:identity_changed"):
        reader(path)


def test_rejects_trailing_bytes_after_deflate_member(tmp_path: Path) -> None:
    path = tmp_path / "trailing.hbr2"
    path.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 120)
        + _raw_deflate(b"payload")
        + b"trailing-garbage"
    )

    with pytest.raises(ReplayFormatError, match="deflate_trailing_data"):
        decompress_replay_payload(path)


def test_rejects_incomplete_deflate_member(tmp_path: Path) -> None:
    compressed = _raw_deflate(b"payload-that-needs-a-complete-stream")
    assert len(compressed) > 1
    path = tmp_path / "incomplete.hbr2"
    path.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 120)
        + compressed[:-1]
    )

    with pytest.raises(ReplayFormatError, match="deflate_incomplete"):
        decompress_replay_payload(path)


def test_rejects_invalid_raw_deflate_bytes(tmp_path: Path) -> None:
    path = tmp_path / "invalid-deflate.hbr2"
    path.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 120)
        + b"not-a-deflate-stream"
    )

    with pytest.raises(ReplayFormatError, match="deflate_error:"):
        decompress_replay_payload(path)
