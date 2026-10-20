import json
import struct
from pathlib import Path

from haxlab.ingestion.pipeline import run_import


def test_import_is_idempotent(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "derived"
    raw.mkdir()

    replay_name = "24-09-26-22h12-aavsmko-deadbeefcafebabe.hbr2"
    replay = raw / replay_name
    replay.write_bytes(struct.pack(">4sII", b"HBR2", 3, 600) + b"test-replay-payload")

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


def test_import_leaves_ambiguous_replay_report_evidence_unmatched(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "derived"
    replay_name = "24-09-26-22h12-ambiguous.hbr2"
    replay_bytes_a = struct.pack(">4sII", b"HBR2", 3, 600) + b"same-sized-payload-a"
    replay_bytes_b = struct.pack(">4sII", b"HBR2", 3, 600) + b"same-sized-payload-b"
    assert len(replay_bytes_a) == len(replay_bytes_b)

    for directory, replay_bytes in (("source-a", replay_bytes_a), ("source-b", replay_bytes_b)):
        target = raw / directory
        target.mkdir(parents=True)
        (target / replay_name).write_bytes(replay_bytes)

    export = {
        "channel": {"id": "726932424172371968"},
        "messages": [
            {
                "id": "ambiguous-report",
                "timestamp": "2026-09-24T22:12:27+02:00",
                "content": "MATCH REPORT",
                "attachments": [
                    {
                        "fileName": replay_name,
                        "fileSizeBytes": len(replay_bytes_a),
                    }
                ],
            }
        ],
    }
    (raw / "channel.json").write_text(
        json.dumps(export, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = run_import(raw, out)

    assert manifest.match_count == 0
    assert manifest.unmatched_reports == ["ambiguous-report"]
    assert manifest.unmatched_replays == [
        f"source-a/{replay_name}",
        f"source-b/{replay_name}",
    ]
    assert (out / "matches.jsonl").read_text(encoding="utf-8") == ""
