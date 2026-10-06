import hashlib
from pathlib import Path

import pytest

from haxlab.hashing import sha256_file


def test_sha256_file_matches_content_digest_across_chunk_boundaries(tmp_path: Path) -> None:
    payload = (b"haxlab-provenance-" * 17) + b"tail"
    path = tmp_path / "evidence.bin"
    path.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()

    assert sha256_file(path) == expected
    assert sha256_file(path, chunk_size=1) == expected
    assert sha256_file(path, chunk_size=7) == expected


@pytest.mark.parametrize(
    "chunk_size",
    [0, -1, True, False, 1.0, "1024", None],
)
def test_sha256_file_rejects_invalid_chunk_size(
    tmp_path: Path,
    chunk_size: object,
) -> None:
    path = tmp_path / "evidence.bin"
    path.write_bytes(b"must-not-hash-with-invalid-cadence")

    with pytest.raises(ValueError, match="chunk_size_must_be_positive_int"):
        sha256_file(path, chunk_size=chunk_size)  # type: ignore[arg-type]
