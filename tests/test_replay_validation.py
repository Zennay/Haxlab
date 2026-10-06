import struct
from pathlib import Path

from haxlab.replay.validation import validate_replay_basic


def test_rejects_positive_frame_header_without_payload(tmp_path: Path) -> None:
    path = tmp_path / "header-only.hbr2"
    path.write_bytes(struct.pack(">4sII", b"HBR2", 3, 600))

    validation = validate_replay_basic(path)

    assert validation.valid is False
    assert validation.reasons == ("truncated_payload",)
    assert validation.version == 3
    assert validation.total_frames == 600
    assert validation.duration_seconds == 10.0


def test_basic_validation_only_requires_payload_presence(tmp_path: Path) -> None:
    path = tmp_path / "payload-present.hbr2"
    path.write_bytes(struct.pack(">4sII", b"HBR2", 3, 600) + b"x")

    validation = validate_replay_basic(path)

    assert validation.valid is True
    assert validation.reasons == ()
    assert validation.total_frames == 600


def test_invalid_total_frames_precedes_payload_presence(tmp_path: Path) -> None:
    path = tmp_path / "zero-frames.hbr2"
    path.write_bytes(struct.pack(">4sII", b"HBR2", 3, 0))

    validation = validate_replay_basic(path)

    assert validation.valid is False
    assert validation.reasons == ("invalid_total_frames",)
