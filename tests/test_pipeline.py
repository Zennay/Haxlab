import json
from pathlib import Path

from haxlab.ingestion.pipeline import run_import


def test_import_is_idempotent(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "derived"
    raw.mkdir()

    replay_name = "24-09-26-22h12-aavsmko-deadbeefcafebabe.hbr2"
    replay = raw / replay_name
    replay.write_bytes(b"HBR2" + b"test-replay-payload")

    export = {
        "channel": {"id": "726932424172371968"},
        "messages": [
            {
                "id": "1552774764110815262",
                "timestamp": "2026-09-24T22:12:27+02:00",
                "content": (
                    "MATCH REPORT SCRIM #20260924T221227749-R2\n"
                    "Red Team 3 - 2 Blue Team\n"
                    "Possession: 🔴 52.34% 🔵 47.66%"
                ),
                "attachments": [
                    {
                        "fileName": "24-09-26-22h12-aavsmko.hbr2",
                        "fileSizeBytes": replay.stat().st_size,
                    }
                ],
            }
        ],
    }
    (raw / "channel.json").write_text(
        json.dumps(export, ensure_ascii=False),
        encoding="utf-8",
    )

    first = run_import(raw, out)
    first_manifest = (out / "manifest.json").read_bytes()
    first_matches = (out / "matches.jsonl").read_bytes()

    second = run_import(raw, out)

    assert first.as_dict() == second.as_dict()
    assert first_manifest == (out / "manifest.json").read_bytes()
    assert first_matches == (out / "matches.jsonl").read_bytes()
    assert first.match_count == 1
    assert not first.unmatched_replays
    assert not first.unmatched_reports
