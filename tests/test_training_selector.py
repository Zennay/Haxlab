from __future__ import annotations

import json
from pathlib import Path

from haxlab.learning.selector import (
    _holdout_bucket,
    build_training_manifest,
    select_players,
)


def test_select_players_uses_conservative_score_per_role() -> None:
    rows = [
        {
            "player_id": "a",
            "name": "A",
            "role": "forward",
            "rating": 60.0,
            "rating_uncertainty": 4.0,
            "matches": 100,
            "minutes": 500.0,
        },
        {
            "player_id": "b",
            "name": "B",
            "role": "forward",
            "rating": 58.0,
            "rating_uncertainty": 1.0,
            "matches": 100,
            "minutes": 500.0,
        },
        {
            "player_id": "c",
            "name": "C",
            "role": "defender",
            "rating": 55.0,
            "rating_uncertainty": 0.5,
            "matches": 100,
            "minutes": 500.0,
        },
    ]

    selected = select_players(
        rows,
        top_fraction_per_role=0.5,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )
    ids = {row["player_id"] for row in selected}

    assert "b" in ids
    assert "c" in ids
    assert "a" not in ids


def test_holdout_partition_is_deterministic() -> None:
    sha = "a" * 64
    first = _holdout_bucket(sha, 10)
    second = _holdout_bucket(sha, 10)

    assert first == second
    assert 0 <= first < 10


def test_training_manifest_selects_quality_replays_and_freezes_split(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "derived" / "state-pass-v4"
    leaderboard_path = tmp_path / "derived" / "leaderboards" / "state-pass-v4.json"
    raw_root = tmp_path / "raw"
    analysis_dir = analysis_root / "aa" / "bb"
    analysis_dir.mkdir(parents=True)
    leaderboard_path.parent.mkdir(parents=True)

    leaderboard_path.write_text(
        json.dumps(
            {
                "analysis_version": "state-pass-v4",
                "rows": [
                    {
                        "player_id": "name:alpha",
                        "name": "Alpha",
                        "role": "forward",
                        "rating": 56.0,
                        "rating_uncertainty": 0.8,
                        "matches": 80,
                        "minutes": 500.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    good_sha = "aabb" + "1" * 60
    bad_sha = "aabb" + "2" * 60
    players = [
        {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900},
        {"id": 8, "name": "Mate", "teamId": 1, "samples": 900},
        {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
        {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
    ]
    good = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"gameStarts": 0, "sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": players,
    }
    short = {
        "schemaVersion": 4,
        "totalFrames": 3000,
        "simulation": {"gameStarts": 0, "sampledStateCount": 1200},
        "featureSummary": {"touches": 20},
        "players": players,
    }
    (analysis_dir / f"{good_sha}.json").write_text(
        json.dumps(good),
        encoding="utf-8",
    )
    (analysis_dir / f"{bad_sha}.json").write_text(
        json.dumps(short),
        encoding="utf-8",
    )

    kwargs = dict(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=raw_root,
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
        holdout_modulus=10,
        holdout_bucket=0,
    )
    first = build_training_manifest(**kwargs)
    second = build_training_manifest(**kwargs)

    first_selected = first["train_replays"] + first["holdout_replays"]
    second_selected = second["train_replays"] + second["holdout_replays"]

    assert len(first_selected) == 1
    assert first_selected[0]["replay_sha256"] == good_sha
    assert first_selected[0]["selected_player_ids"] == ["name:alpha"]
    assert first_selected[0]["selected_players"] == [
        {
            "replay_player_id": 7,
            "identity": "name:alpha",
            "samples": 900,
        }
    ]
    assert first["stats"]["quality_rejected"] == 1
    assert [
        row["replay_sha256"] for row in first["train_replays"]
    ] == [
        row["replay_sha256"] for row in second["train_replays"]
    ]
    assert [
        row["replay_sha256"] for row in first["holdout_replays"]
    ] == [
        row["replay_sha256"] for row in second["holdout_replays"]
    ]


def test_quality_accepts_replay_without_game_start_when_state_exists() -> None:
    from haxlab.learning.selector import _replay_quality

    ok, reasons = _replay_quality(
        {
            "schemaVersion": 4,
            "totalFrames": 18000,
            "simulation": {
                "gameStarts": 0,
                "sampledStateCount": 3000,
            },
            "featureSummary": {"touches": 100},
            "players": [
                {"name": "A", "teamId": 1},
                {"name": "B", "teamId": 1},
                {"name": "C", "teamId": 2},
                {"name": "D", "teamId": 2},
            ],
        }
    )

    assert ok is True
    assert reasons == []


def test_training_manifest_excludes_selected_spectator(tmp_path: Path) -> None:
    analysis_root = tmp_path / "state-pass-v4"
    leaderboard = tmp_path / "leaderboard.json"
    analysis_root.mkdir()

    leaderboard.write_text(
        json.dumps(
            {
                "analysis_version": "state-pass-v4",
                "rows": [
                    {
                        "player_id": "name:alpha",
                        "name": "Alpha",
                        "role": "midfield",
                        "rating": 55.0,
                        "rating_uncertainty": 0.5,
                        "matches": 50,
                        "minutes": 300.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    sha = "f" * 64
    (analysis_root / f"{sha}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 18000,
                "simulation": {"sampledStateCount": 3000},
                "featureSummary": {"touches": 100},
                "players": [
                    {"id": 1, "name": "Alpha", "teamId": 0, "samples": 0},
                    {"id": 2, "name": "B", "teamId": 1, "samples": 900},
                    {"id": 3, "name": "C", "teamId": 1, "samples": 900},
                    {"id": 4, "name": "D", "teamId": 2, "samples": 900},
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard,
        raw_root=tmp_path / "raw",
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )

    assert manifest["train_replays"] == []
    assert manifest["holdout_replays"] == []
