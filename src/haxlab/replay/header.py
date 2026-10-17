from __future__ import annotations

import errno
import os
import stat
import struct
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


HBR2_MAGIC = b"HBR2"
SUPPORTED_VERSION = 3
LOGIC_HZ = 60.0


@dataclass(frozen=True)
class ReplayHeader:
    version: int
    total_frames: int

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / LOGIC_HZ


class ReplayFormatError(ValueError):
    pass


@contextmanager
def _open_regular_replay(path: Path) -> Iterator[BinaryIO]:
    """Open one stable regular replay file without following symlinks."""

    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode):
        raise ReplayFormatError("unsafe_replay_path:symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ReplayFormatError("unsafe_replay_path:not_regular")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ReplayFormatError("unsafe_replay_path:nofollow_unsupported")

    flags = os.O_RDONLY | nofollow
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ReplayFormatError("unsafe_replay_path:symlink") from exc
        raise

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ReplayFormatError("unsafe_replay_path:not_regular")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ReplayFormatError("unsafe_replay_path:identity_changed")
        handle = os.fdopen(fd, "rb")
    except Exception:
        os.close(fd)
        raise

    with handle:
        yield handle


def read_replay_header(path: Path) -> ReplayHeader:
    with _open_regular_replay(path) as handle:
        header = handle.read(12)

    if len(header) != 12:
        raise ReplayFormatError("truncated_header")

    magic, version, total_frames = struct.unpack(">4sII", header)

    if magic != HBR2_MAGIC:
        raise ReplayFormatError("invalid_magic")

    if version != SUPPORTED_VERSION:
        raise ReplayFormatError(f"unsupported_version:{version}")

    return ReplayHeader(version=version, total_frames=total_frames)


def decompress_replay_payload(path: Path) -> bytes:
    """Return the decompressed v3 payload.

    HBR2 v3 stores the payload after the 12-byte header as raw DEFLATE.
    """
    with _open_regular_replay(path) as handle:
        data = handle.read()

    if len(data) < 12:
        raise ReplayFormatError("truncated_header")

    # Validate header/version before attempting decompression.
    magic, version, _ = struct.unpack(">4sII", data[:12])
    if magic != HBR2_MAGIC:
        raise ReplayFormatError("invalid_magic")
    if version != SUPPORTED_VERSION:
        raise ReplayFormatError(f"unsupported_version:{version}")

    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        payload = decompressor.decompress(data[12:])
        payload += decompressor.flush()
    except zlib.error as exc:
        raise ReplayFormatError(f"deflate_error:{exc}") from exc

    if not decompressor.eof:
        raise ReplayFormatError("deflate_incomplete")
    if decompressor.unconsumed_tail:
        raise ReplayFormatError("deflate_unconsumed_data")
    if decompressor.unused_data:
        raise ReplayFormatError("deflate_trailing_data")

    return payload
