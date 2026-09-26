from __future__ import annotations

import json
from pathlib import Path

from haxlab.learning.recovery_dataset import (
    DATASET_SCHEMA,
    build_dataset,
    split_for_fingerprint,
    state_fingerprint,
    write_dataset,
)


def _write_trace(path: Path, rows: list[dict]) -> None:
    header = {
        "schema": "haxlab-recovery-trace-v1",
        "source_ref": "test-sha",
        "provenance": {
            "challenger_sha256": "a" * 64,
            "scenarios_sha256": "b" * 64,
            "stadium_sha256": "c" * 64,
        },
        "config": {"seed": 1337},
    }
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _row(x: float, failure: str, *, role: str = "am") -> dict:
    return {
        "schema": "haxlab-recovery-trace-row-v1",
        "mode": "full_team",
        "tested_role": None,
        "role": role,
        "scenario_index": 1,
        "test_team_id": 1,
        "repeat_index": 0,
        "seed": 1337,
        "tick": 120,
        "failure_types": [failure],
        "diagnostics": {"boundary": failure == "boundary"},
        "recovery_window": [
            {
                "tick": 120,
                "features": {"player_x": x, "ball_x": 5.0},
                "action": {"dir_x": 1, "dir_y": 0, "kick": False},
                "canonical_action": {"dir_x": 1, "dir_y": 0},
                "context": {"distance": 200.0, "angle": 0.0},
                "player_position": {"x": x, "y": 1.0},
                "ball_position": {"x": 5.0, "y": 1.0},
            }
        ],
    }


def test_fingerprint_quantizes_and_split_is_deterministic() -> None:
    a = _row(10.0001, "boundary")
    b = _row(10.0002, "far_stall")
    assert state_fingerprint(a) == state_fingerprint(b)
    fp = state_fingerprint(a)
    assert split_for_fingerprint(fp) == split_for_fingerprint(fp)


def test_builder_dedupes_and_merges_failure_labels(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, [_row(10.0, "boundary"), _row(10.0, "far_stall")])
    manifest, splits = build_dataset([trace])
    rows = [row for values in splits.values() for row in values]
    assert manifest["schema"] == DATASET_SCHEMA
    assert manifest["human_replay_data_included"] is False
    assert manifest["raw_trace_rows"] == 2
    assert manifest["deduplicated_rows"] == 1
    assert manifest["duplicate_rows_removed"] == 1
    assert rows[0]["failure_types"] == ["boundary", "far_stall"]
    assert len(rows[0]["sources"]) == 1


def test_write_dataset_is_reproducible(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, [_row(float(i), "role_deviation") for i in range(20)])
    manifest, splits = build_dataset([trace])
    first = write_dataset(tmp_path, "v1", manifest, splits)
    first_bytes = first.read_bytes()
    first_files = {
        name: (tmp_path / "v1" / name).read_bytes()
        for name in ("train.jsonl", "validation.jsonl", "evaluation.jsonl")
    }
    second = write_dataset(tmp_path, "v1", manifest, splits)
    assert second.read_bytes() == first_bytes
    assert {
        name: (tmp_path / "v1" / name).read_bytes()
        for name in first_files
    } == first_files