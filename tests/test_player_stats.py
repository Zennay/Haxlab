from __future__ import annotations

import json
from pathlib import Path

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
