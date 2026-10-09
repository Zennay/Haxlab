from __future__ import annotations

import hashlib

import pytest

from haxlab.hashing import sha256_file


class _IntSubclass(int):
    pass


@pytest.mark.parametrize(
    "chunk_size",
    [0, -1, True, False, 1.0, "1", None, _IntSubclass(1)],
)
def test_sha256_file_rejects_invalid_chunk_size_before_io(tmp_path, chunk_size):
    missing = tmp_path / "missing.bin"

    with pytest.raises(ValueError, match="positive native integer"):
        sha256_file(missing, chunk_size=chunk_size)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 64, 1024])
def test_sha256_file_digest_is_independent_of_valid_chunk_boundary(tmp_path, chunk_size):
    payload = bytes(range(256)) * 5 + b"haxlab"
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)

    assert sha256_file(path, chunk_size=chunk_size) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_default_behavior_matches_hashlib(tmp_path):
    payload = b"default-boundary-contract"
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)

    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()
