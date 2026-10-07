from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from haxlab.runtime.player_stats import add_proxy_score, collect


def _write_replay(root: Path, name: str, players: list[dict]) -> None:
    path = root / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "players": players,
            }
        ),
        encoding="utf-8",
    )


def test_collect_merges_normalized_display_names(tmp_path: Path) -> None:
    _write_replay(
        tmp_path,
        "a",
        [
            {
                "name": " Alice ",
                "samples": 600,
                "nearestBallSamples": 120,
                "closeBallSamples": 60,
                "inputEvents": 30,
                "kickEvents": 12,
                "kickPressedInputs": 10,
            }
        ],
    )
    _write_replay(
        tmp_path,
        "b",
        [
            {
                "name": "alice",
                "samples": 600,
                "nearestBallSamples": 180,
                "closeBallSamples": 90,
                "inputEvents": 50,
                "kickEvents": 18,
                "kickPressedInputs": 15,
            }
        ],
    )

    rows = collect(tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["matches"] == 2
    assert row["active_minutes"] == 2.0
    assert row["kick_events"] == 30
    assert row["nearest_ball_pct"] == 25.0
    assert row["close_ball_pct"] == 12.5


def test_proxy_score_rewards_more_involvement() -> None:
    rows = [
        {
            "matches": 40,
            "kicks_per_min": 20.0,
            "close_ball_pct": 15.0,
            "nearest_ball_pct": 25.0,
        },
        {
            "matches": 40,
            "kicks_per_min": 5.0,
            "close_ball_pct": 4.0,
            "nearest_ball_pct": 8.0,
        },
    ]

    add_proxy_score(rows)

    assert rows[0]["involvement_score"] > rows[1]["involvement_score"]


def test_collect_uses_auth_hash_across_name_changes(tmp_path: Path) -> None:
    _write_replay(
        tmp_path,
        "auth-a",
        [
            {
                "name": "Old Name",
                "authHash": "abc123",
                "samples": 600,
                "nearestBallSamples": 100,
                "closeBallSamples": 50,
                "inputEvents": 30,
                "kickEvents": 10,
                "kickPressedInputs": 8,
            }
        ],
    )
    _write_replay(
        tmp_path,
        "auth-b",
        [
            {
                "name": "New Name",
                "authHash": "abc123",
                "samples": 600,
                "nearestBallSamples": 120,
                "closeBallSamples": 60,
                "inputEvents": 35,
                "kickEvents": 12,
                "kickPressedInputs": 9,
            }
        ],
    )

    _write_replay(
        tmp_path,
        "auth-c",
        [
            {
                "name": "New Name",
                "authHash": "abc123",
                "samples": 600,
                "nearestBallSamples": 110,
                "closeBallSamples": 55,
                "inputEvents": 32,
                "kickEvents": 11,
                "kickPressedInputs": 8,
            }
        ],
    )

    rows = collect(tmp_path)

    assert len(rows) == 1
    assert rows[0]["matches"] == 3
    assert rows[0]["name"] == "New Name"



@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("samples", "600"),
        ("samples", True),
        ("samples", -1),
        ("nearestBallSamples", -1),
        ("touchProgressionSum", math.nan),
        ("touchProgressionSum", math.inf),
    ],
)
def test_collect_rejects_malformed_metric_artifacts(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    player = {
        "name": "Alice",
        "samples": 600,
        "nearestBallSamples": 120,
        "closeBallSamples": 60,
    }
    player[field] = value
    _write_replay(tmp_path, "bad", [player])

    assert collect(tmp_path) == []


def test_collect_rejects_sample_subcounts_above_total(tmp_path: Path) -> None:
    _write_replay(
        tmp_path,
        "bad-bounds",
        [
            {
                "name": "Alice",
                "samples": 10,
                "nearestBallSamples": 11,
                "closeBallSamples": 5,
            }
        ],
    )

    assert collect(tmp_path) == []


def test_collect_rejects_malformed_container_shapes(tmp_path: Path) -> None:
    (tmp_path / "wrong-schema.json").write_text(
        json.dumps({"schemaVersion": "3", "players": []}),
        encoding="utf-8",
    )
    (tmp_path / "wrong-players.json").write_text(
        json.dumps({"schemaVersion": 3, "players": {}}),
        encoding="utf-8",
    )
    (tmp_path / "wrong-root.json").write_text(
        json.dumps([]),
        encoding="utf-8",
    )

    assert collect(tmp_path) == []


def test_collect_rejects_entire_artifact_before_partial_aggregation(
    tmp_path: Path,
) -> None:
    _write_replay(
        tmp_path,
        "valid",
        [
            {
                "name": "Alice",
                "samples": 600,
                "nearestBallSamples": 120,
                "closeBallSamples": 60,
            }
        ],
    )
    _write_replay(
        tmp_path,
        "partially-malformed",
        [
            {
                "name": "Alice",
                "samples": 600,
                "nearestBallSamples": 120,
                "closeBallSamples": 60,
            },
            {
                "name": "Bob",
                "samples": "600",
            },
        ],
    )

    rows = collect(tmp_path)

    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert rows[0]["matches"] == 1
    assert rows[0]["samples"] == 600


def test_collect_accepts_schema_v4_without_coercion(tmp_path: Path) -> None:
    path = tmp_path / "v4.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "players": [
                    {
                        "name": "Alice",
                        "samples": 600,
                        "nearestBallSamples": 120,
                        "closeBallSamples": 60,
                        "touchProgressionSum": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = collect(tmp_path)

    assert len(rows) == 1
    assert rows[0]["matches"] == 1
    assert rows[0]["touch_progression_sum"] == 2.0
