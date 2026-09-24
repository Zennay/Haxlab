from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


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


def read_replay_header(path: Path) -> ReplayHeader:
    with path.open("rb") as handle:
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
    with path.open("rb") as handle:
        data = handle.read()

    if len(data) < 12:
        raise ReplayFormatError("truncated_header")

    # Validate header/version before attempting decompression.
    magic, version, _ = struct.unpack(">4sII", data[:12])
    if magic != HBR2_MAGIC:
        raise ReplayFormatError("invalid_magic")
    if version != SUPPORTED_VERSION:
        raise ReplayFormatError(f"unsupported_version:{version}")

    try:
        return zlib.decompress(data[12:], -zlib.MAX_WBITS)
    except zlib.error as exc:
        raise ReplayFormatError(f"deflate_error:{exc}") from exc
