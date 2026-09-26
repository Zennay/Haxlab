from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.learning.recovery_dataset import (
    DATASET_SCHEMA,
    build_dataset,
    rollout_group_fingerprint,
    split_for_fingerprint,
    state_fingerprint,
    write_dataset,
)


SOURCE_SHA = "d" * 64


def _header(source_sha: str = SOURCE_SHA) -> dict:
    return {
        "schema": "haxlab-recovery-trace-v1",
        "source_ref": "test-sha",
        "provenance": {
            "source_replay_sha256": source_sha,
            "challenger_sha256": "a" * 64,
            "scenarios_sha256": "b" * 64,
            "stadium_sha256": "c" * 64,
        },
        "config": {"seed": 1337},
    }


def _write_trace(
    path: Path,
    rows: list[dict],
    *,
    source_sha: str = SOURCE_SHA,
) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_header(source_sha)) + "\n")
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _row(
    x: float,
    failure: str,
    *,
    role: str = "am",
    scenario_index: int = 1,
    side: int = 1,
    tested_role: str | None = None,
) -> dict:
    return {
        "schema": "haxlab-recovery-trace-row-v1",
        "mode": "full_team" if tested_role is None else "plug_and_play",
        "tested_role": tested_role,
        "role": role,
        "scenario_index": scenario_index,
        "test_team_id": side,
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


def _all_rows(splits: dict[str, list[dict]]) -> list[dict]:
    return [row for values in splits.values() for row in values]


def test_fingerprint_quantizes_and_split_is_deterministic() -> None:
    a = _row(10.0001, "boundary")
    b = _row(10.0002, "far_stall")
    assert state_fingerprint(a) == state_fingerprint(b)
    fp = state_fingerprint(a)
    assert split_for_fingerprint(fp) == split_for_fingerprint(fp)


def test_rollout_group_split_keeps_each_rollout_together(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    rows = [
        _row(10.0, "boundary", scenario_index=1),
        _row(11.0, "role_deviation", scenario_index=1),
        _row(20.0, "boundary", scenario_index=2),
        _row(21.0, "far_stall", scenario_index=2),
    ]
    _write_trace(trace, rows)
    manifest, splits = build_dataset([trace])
    assert manifest["rollout_group_count"] == 2

    groups: dict[str, set[str]] = {}
    for row in _all_rows(splits):
        assert len(row["sources"]) == 1
        source = row["sources"][0]
        group = source["rollout_group_fingerprint"]
        groups.setdefault(group, set()).add(row["split"])
        assert row["split"] == source["rollout_group_split"]
    assert groups
    assert all(len(values) == 1 for values in groups.values())


def test_builder_dedupes_and_merges_failure_labels(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, [_row(10.0, "boundary"), _row(10.0, "far_stall")])
    manifest, splits = build_dataset([trace])
    rows = _all_rows(splits)
    assert manifest["schema"] == DATASET_SCHEMA
    assert manifest["human_replay_data_included"] is False
    assert manifest["raw_trace_rows"] == 2
    assert manifest["deduplicated_rows"] == 1
    assert manifest["duplicate_rows_removed"] == 1
    assert manifest["input_traces"][0]["source_replay_sha256"] == SOURCE_SHA
    assert rows[0]["failure_types"] == ["boundary", "far_stall"]
    assert len(rows[0]["sources"]) == 1


def test_builder_rejects_forbidden_source_replay(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, [_row(10.0, "boundary")])
    with pytest.raises(ValueError, match="forbidden recovery source replay"):
        build_dataset([trace], forbidden_source_sha256s={SOURCE_SHA})


def test_missing_source_replay_sha_fails_closed(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    header = _header()
    del header["provenance"]["source_replay_sha256"]
    trace.write_text(
        json.dumps(header) + "\n" + json.dumps(_row(10.0, "boundary")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source_replay_sha256"):
        build_dataset([trace])


def test_cross_group_duplicate_state_is_emitted_once(tmp_path: Path) -> None:
    header = _header()
    first = _row(10.0, "boundary", scenario_index=1)
    first_group = rollout_group_fingerprint(header, first)
    other = None
    for scenario_index in range(2, 500):
        candidate = _row(10.0, "far_stall", scenario_index=scenario_index)
        candidate_group = rollout_group_fingerprint(header, candidate)
        if split_for_fingerprint(candidate_group) != split_for_fingerprint(first_group):
            other = candidate
            break
    assert other is not None
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, [first, other])
    manifest, splits = build_dataset([trace])
    rows = _all_rows(splits)
    assert len(rows) == 1
    assert manifest["cross_group_duplicate_states"] == 1
    assert manifest["cross_split_duplicate_states_resolved"] == 1
    assert manifest["cross_split_duplicate_observations_dropped"] == 1
    assert len(rows[0]["sources"]) == 1
    assert rows[0]["sources"][0]["rollout_group_split"] == rows[0]["split"]


def test_write_dataset_is_reproducible_and_immutable(tmp_path: Path) -> None:
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

    changed = tmp_path / "changed.jsonl"
    _write_trace(changed, [_row(999.0, "boundary")])
    changed_manifest, changed_splits = build_dataset([changed])
    with pytest.raises(FileExistsError, match="different content"):
        write_dataset(tmp_path, "v1", changed_manifest, changed_splits)
    assert first.read_bytes() == first_bytes
