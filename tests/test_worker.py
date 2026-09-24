import struct
import zlib
from pathlib import Path

from haxlab.hashing import sha256_file
from haxlab.runtime.state import RuntimeState
from haxlab.runtime.worker import process_batch


def _valid_hbr2(total_frames: int = 3600) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    payload = compressor.compress(b"payload-data") + compressor.flush()
    return struct.pack(">4sII", b"HBR2", 3, total_frames) + payload


def test_worker_probes_unprocessed_replay(tmp_path: Path) -> None:
    replay = tmp_path / "raw" / "sample.hbr2"
    replay.parent.mkdir()
    replay.write_bytes(_valid_hbr2())

    sha = sha256_file(replay)
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=replay.stat().st_size,
        )

        result = process_batch(state, batch_size=10)
        status = state.status_snapshot()

    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert status["processing_ok"] == 1
    assert status["processing_pending"] == 0
    assert status["total_frames_probed"] == 3600
