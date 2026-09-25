from __future__ import annotations

import json
from pathlib import Path

from haxlab.skill.leaderboard import _raw_metrics, build_leaderboard


def _player(
    player_id: int,
    name: str,
    team_id: int,
    average_x: float,
    *,
    retained: int,
    lost: int,
    recoveries: int,
    goals: int,
    assists: int,
    progression: float,
) -> dict:
    return {
        "id": player_id,
        "name": name,
        "teamId": team_id,
        "samples": 6000,
        "averageX": average_x,
        "averageY": 0.0,
        "nearestBallSamples": 1200,
        "closeBallSamples": 500,
        "kickEvents": retained + lost + recoveries,
        "inferredRetainedChains": retained,
        "inferredLostChains": lost,
        "inferredRecoveries": recoveries,
        "inferredGoals": goals,
        "inferredAssists": assists,
        "progressionEvents": max(3, retained),
        "progressionSum": progression,
    }


def _write_match(path: Path, players: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "totalFrames": 36000,
                "simulation": {"sampleEveryTicks": 6},
                "players": players,
            }
        ),
        encoding="utf-8",
    )


def test_skill_leaderboard_builds_conservative_player_estimates(tmp_path: Path) -> None:
    for i in range(35):
        _write_match(
            tmp_path / f"{i}.json",
            [
                _player(
                    1,
                    "Alpha",
                    1,
                    -40,
                    retained=25,
                    lost=3,
                    recoveries=8,
                    goals=1,
                    assists=1,
                    progression=300,
                ),
                _player(
                    2,
                    "Beta",
                    1,
                    0,
                    retained=15,
                    lost=10,
                    recoveries=3,
                    goals=0,
                    assists=0,
                    progression=40,
                ),
                _player(
                    3,
                    "Gamma",
                    1,
                    40,
                    retained=18,
                    lost=8,
                    recoveries=2,
                    goals=0,
                    assists=0,
                    progression=100,
                ),
                _player(
                    4,
                    "Opp A",
                    2,
                    40,
                    retained=15,
                    lost=8,
                    recoveries=3,
                    goals=0,
                    assists=0,
                    progression=40,
                ),
                _player(
                    5,
                    "Opp B",
                    2,
                    0,
                    retained=15,
                    lost=8,
                    recoveries=3,
                    goals=0,
                    assists=0,
                    progression=40,
                ),
                _player(
                    6,
                    "Opp C",
                    2,
                    -40,
                    retained=15,
                    lost=8,
                    recoveries=3,
                    goals=0,
                    assists=0,
                    progression=40,
                ),
            ],
        )

    rows = build_leaderboard(tmp_path)
    by_name = {row["name"]: row for row in rows}

    assert by_name["Alpha"]["matches"] == 35
    assert by_name["Alpha"]["role"] == "defender"
    assert by_name["Alpha"]["rating"] > by_name["Beta"]["rating"]
    assert by_name["Alpha"]["rating_uncertainty"] > 0
    assert by_name["Alpha"]["dimensions"]["retention"]["effective_weight"] > 0


def test_schema_v4_prefers_touch_chain_evidence() -> None:
    player = {
        "teamTouchTransfersOut": 9,
        "turnovers": 1,
        "recoveries": 4,
        "touchGoals": 2,
        "touchAssists": 3,
        "touchProgressionEvents": 5,
        "touchProgressionSum": 50.0,
        "pressuredTransitions": 4,
        "retainedUnderPressure": 3,
        "samples": 600,
        "nearestBallSamples": 120,
        # Deliberately contradictory v3 fallback values.
        "inferredRetainedChains": 0,
        "inferredLostChains": 10,
        "inferredRecoveries": 0,
        "inferredGoals": 0,
        "inferredAssists": 0,
        "progressionEvents": 5,
        "progressionSum": -50.0,
    }

    metrics = _raw_metrics(player, 10.0, schema_version=4)

    assert metrics["retention"] == 0.9
    assert metrics["progression"] == 10.0
    assert metrics["defending"] == 0.4
    assert metrics["pressure_recovery"] == 0.75
    assert metrics["risk_management"] == -0.1
