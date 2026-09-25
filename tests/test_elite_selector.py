from __future__ import annotations

import json
from pathlib import Path

from haxlab.learning.elite_selector import (
    ROLE_IDS,
    _split_bucket,
    build_elite_manifest,
    select_elite_players,
)


def _row(
    player_id: str,
    name: str,
    role: str,
    rating: float,
    uncertainty: float,
) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "role": role,
        "rating": rating,
        "rating_uncertainty": uncertainty,
        "matches": 100,
        "minutes": 600.0,
    }


def test_elite_selection_is_role_based_and_conservative() -> None:
    rows = [
        _row("a", "A", "gk", 60.0, 5.0),
        _row("b", "B", "gk", 58.0, 1.0),
        _row("c", "C", "dm", 57.0, 1.0),
        _row("d", "D", "am", 57.0, 1.0),
        _row("e", "E", "st", 57.0, 1.0),
    ]
    selected = select_elite_players(
        rows,
        top_fraction_per_role=0.5,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )
    ids = {row["player_id"] for row in selected}
    assert "b" in ids
    assert "a" not in ids
    assert {"c", "d", "e"} <= ids


def test_aliases_canonicalize_identity_without_losing_source_id() -> None:
    rows = [
        _row("name:misio", "misio", "am", 58.0, 0.5),
        _row("name:sekai", "sekai", "am", 57.0, 0.5),
    ]
    selected = select_elite_players(
        rows,
        aliases={"misio": "sekai"},
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )
    by_id = {row["player_id"]: row for row in selected}
    assert by_id["name:misio"]["canonical_identity"] == "alias:sekai"
    assert by_id["name:sekai"]["canonical_identity"] == "alias:sekai"


def test_elite_split_is_deterministic() -> None:
    sha = "a" * 64
    assert _split_bucket(sha, 20) == _split_bucket(sha, 20)


def test_elite_manifest_builds_three_way_split_and_local_roles(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "derived" / "state-pass-v4"
    leaderboard = tmp_path / "leaderboard.json"
    raw_root = tmp_path / "raw"
    analysis_root.mkdir(parents=True)

    leaderboard.write_text(
        json.dumps(
            {
                "analysis_version": "state-pass-v4",
                "rows": [
                    _row("name:elite", "Elite", "dm", 58.0, 0.5),
                ],
            }
        ),
        encoding="utf-8",
    )

    sha = "1" * 64
    players = [
        {"id": 1, "name": "GK", "teamId": 1, "samples": 3000, "averageX": -160},
        {"id": 2, "name": "Elite", "teamId": 1, "samples": 3000, "averageX": -50},
        {"id": 3, "name": "AM", "teamId": 1, "samples": 3000, "averageX": 50},
        {"id": 4, "name": "ST", "teamId": 1, "samples": 3000, "averageX": 150},
        {"id": 5, "name": "GK2", "teamId": 2, "samples": 3000, "averageX": 160},
        {"id": 6, "name": "DM2", "teamId": 2, "samples": 3000, "averageX": 50},
        {"id": 7, "name": "AM2", "teamId": 2, "samples": 3000, "averageX": -50},
        {"id": 8, "name": "ST2", "teamId": 2, "samples": 3000, "averageX": -150},
    ]
    (analysis_root / f"{sha}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 36000,
                "simulation": {
                    "sampledStateCount": 6000,
                    "sampleEveryTicks": 6,
                },
                "featureSummary": {"touches": 300},
                "players": players,
            }
        ),
        encoding="utf-8",
    )

    manifest = build_elite_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard,
        raw_root=raw_root,
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )
    entries = (
        manifest["train_replays"]
        + manifest["validation_replays"]
        + manifest["holdout_replays"]
    )
    assert len(entries) == 1
    selected = entries[0]["selected_players"]
    assert selected[0]["name"] == "Elite"
    assert selected[0]["role"] == "dm"
    assert selected[0]["role_id"] == ROLE_IDS["dm"]
    assert selected[0]["skill_weight"] > 1.0
