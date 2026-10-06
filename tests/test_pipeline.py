import json
import math
import struct
from pathlib import Path

import pytest

from haxlab.ingestion.pipeline import _write_json, _write_jsonl, run_import


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
    assert not list(out.glob(".*.tmp"))


def test_atomic_json_write_preserves_previous_artifact_on_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    previous = b'{"stable": true}\n'
    path.write_bytes(previous)

    with pytest.raises(TypeError):
        _write_json(path, {"bad": object()})

    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".manifest.json.*.tmp"))


def test_atomic_jsonl_write_preserves_previous_artifact_on_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "matches.jsonl"
    previous = b'{"match_id": "stable"}\n'
    path.write_bytes(previous)

    with pytest.raises(TypeError):
        _write_jsonl(path, [{"match_id": "next"}, {"bad": object()}])

    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".matches.jsonl.*.tmp"))


@pytest.mark.parametrize(
    "value",
    [
        -0.01,
        1.01,
        math.nan,
        math.inf,
        -math.inf,
        True,
        False,
        "0.65",
        None,
    ],
)
def test_import_rejects_invalid_minimum_match_confidence(
    tmp_path: Path,
    value: object,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()

    with pytest.raises(
        ValueError,
        match=r"minimum_match_confidence must be a finite native number in \[0, 1\]",
    ):
        run_import(
            raw,
            tmp_path / "derived",
            minimum_match_confidence=value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("value", [0, 0.65, 1])
def test_import_accepts_bounded_native_minimum_match_confidence(
    tmp_path: Path,
    value: int | float,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()

    manifest = run_import(
        raw,
        tmp_path / f"derived-{value}",
        minimum_match_confidence=value,
    )

    assert manifest.replay_count == 0
    assert manifest.report_count == 0
    assert manifest.match_count == 0


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

def test_import_rejects_missing_export_root_without_publishing(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    out = tmp_path / "derived"

    with pytest.raises(FileNotFoundError, match="export_root_missing"):
        run_import(missing, out)

    assert not out.exists()


def test_import_rejects_non_directory_export_root_without_publishing(tmp_path: Path) -> None:
    source = tmp_path / "export.json"
    source.write_text("{}", encoding="utf-8")
    out = tmp_path / "derived"

    with pytest.raises(NotADirectoryError, match="export_root_not_directory"):
        run_import(source, out)

    assert not out.exists()
