from __future__ import annotations

import json
from pathlib import Path
import sys

from haxlab.skill.leaderboard import (\n    _bounded_match_contexts,\n    _raw_metrics,\n    build_leaderboard,\n    main,\n)


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


def test_skill_cli_can_write_json_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_match(
        tmp_path / "one.json",
        [
            _player(
                1,
                "Alpha",
                1,
                -40,
                retained=8,
                lost=2,
                recoveries=2,
                goals=1,
                assists=0,
                progression=40,
            )
        ],
    )
    output = tmp_path / "leaderboard.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-skill",
            "--root",
            str(tmp_path),
            "--top",
            "10",
            "--min-matches",
            "1",
            "--min-minutes",
            "0",
            "--format",
            "json",
            "--output",
            str(output),
        ],
    )

    assert main() == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))

    assert printed["schema"] == "haxlab-skill-leaderboard-v1"
    assert saved["schema"] == "haxlab-skill-leaderboard-v1"
    assert saved["rows"][0]["name"] == "Alpha"


def test_match_context_is_bounded_and_action_based() -> None:
    dims = {
        "retention": 3.0,
        "progression": 3.0,
        "creation": 3.0,
        "finishing": 3.0,
        "defending": 3.0,
        "positioning": 3.0,
        "pressure_recovery": 3.0,
        "risk_management": 3.0,
    }
    weak = {key: -value for key, value in dims.items()}
    neutral = {key: 0.0 for key in dims}

    rows = [
        {
            "player_id": "target",
            "match_id": "strong-match",
            "team_id": 1,
            "minutes": 10.0,
            "normalized": neutral,
        },
        {
            "player_id": "mate-a",
            "match_id": "strong-match",
            "team_id": 1,
            "minutes": 10.0,
            "normalized": neutral,
        },
        {
            "player_id": "strong-a",
            "match_id": "strong-match",
            "team_id": 2,
            "minutes": 10.0,
            "normalized": dims,
        },
        {
            "player_id": "strong-b",
            "match_id": "strong-match",
            "team_id": 2,
            "minutes": 10.0,
            "normalized": dims,
        },
        {
            "player_id": "target",
            "match_id": "weak-match",
            "team_id": 1,
            "minutes": 10.0,
            "normalized": neutral,
        },
        {
            "player_id": "mate-b",
            "match_id": "weak-match",
            "team_id": 1,
            "minutes": 10.0,
            "normalized": neutral,
        },
        {
            "player_id": "weak-a",
            "match_id": "weak-match",
            "team_id": 2,
            "minutes": 10.0,
            "normalized": weak,
        },
        {
            "player_id": "weak-b",
            "match_id": "weak-match",
            "team_id": 2,
            "minutes": 10.0,
            "normalized": weak,
        },
    ]

    contexts = _bounded_match_contexts(rows)
    _, strong_opponents = contexts[("strong-match", "target")]
    _, weak_opponents = contexts[("weak-match", "target")]

    assert 0.0 < strong_opponents <= 1.0
    assert -1.0 <= weak_opponents < 0.0
    assert strong_opponents > weak_opponents
