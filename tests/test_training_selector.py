from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

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
    good_path = analysis_dir / f"{good_sha}.json"
    assert first_selected[0]["analysis_sha256"] == hashlib.sha256(
        good_path.read_bytes()
    ).hexdigest()
    assert first_selected[0]["analysis_size_bytes"] == good_path.stat().st_size
    assert first["leaderboard_sha256"] == hashlib.sha256(
        leaderboard_path.read_bytes()
    ).hexdigest()
    assert first["leaderboard_size_bytes"] == leaderboard_path.stat().st_size
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


def test_training_manifest_provenance_tracks_exact_input_bytes(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "derived" / "state-pass-v4"
    leaderboard_path = tmp_path / "derived" / "leaderboards" / "state-pass-v4.json"
    raw_root = tmp_path / "raw"
    analysis_root.mkdir(parents=True)
    leaderboard_path.parent.mkdir(parents=True)

    leaderboard = {
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
    leaderboard_path.write_text(json.dumps(leaderboard), encoding="utf-8")

    sha = "a" * 64
    analysis_path = analysis_root / f"{sha}.json"
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [
            {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900},
            {"id": 8, "name": "Mate", "teamId": 1, "samples": 900},
            {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
            {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
        ],
    }
    analysis_path.write_text(json.dumps(payload), encoding="utf-8")

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
    first_entry = (first["train_replays"] + first["holdout_replays"])[0]

    analysis_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    leaderboard_path.write_text(
        json.dumps(leaderboard, indent=2) + "\n",
        encoding="utf-8",
    )

    second = build_training_manifest(**kwargs)
    second_entry = (second["train_replays"] + second["holdout_replays"])[0]

    assert first_entry["replay_sha256"] == second_entry["replay_sha256"]
    assert first_entry["analysis_sha256"] != second_entry["analysis_sha256"]
    assert first["leaderboard_sha256"] != second["leaderboard_sha256"]
    assert second_entry["analysis_sha256"] == hashlib.sha256(
        analysis_path.read_bytes()
    ).hexdigest()
    assert second["leaderboard_sha256"] == hashlib.sha256(
        leaderboard_path.read_bytes()
    ).hexdigest()



def test_training_manifest_rejects_noncanonical_analysis_filename(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    leaderboard_path = tmp_path / "leaderboard.json"
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
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [
            {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900},
            {"id": 8, "name": "Mate", "teamId": 1, "samples": 900},
            {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
            {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
        ],
    }
    (analysis_root / "not-a-replay-sha.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=tmp_path / "raw",
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )

    assert manifest["train_replays"] == []
    assert manifest["holdout_replays"] == []
    assert manifest["stats"]["analysis_files_scanned"] == 1
    assert manifest["stats"]["quality_rejected"] == 1
    assert manifest["stats"]["quality_rejection_reasons"] == {
        "invalid_analysis_provenance": 1
    }


def test_training_manifest_rejects_symlinked_analysis_artifact(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    leaderboard_path = tmp_path / "leaderboard.json"
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
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [
            {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900},
            {"id": 8, "name": "Mate", "teamId": 1, "samples": 900},
            {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
            {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
        ],
    }
    target = tmp_path / "external.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    link = analysis_root / ("a" * 64 + ".json")
    link.symlink_to(target)

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=tmp_path / "raw",
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )

    assert manifest["train_replays"] == []
    assert manifest["holdout_replays"] == []
    assert manifest["stats"]["quality_rejected"] == 1
    assert manifest["stats"]["quality_rejection_reasons"] == {
        "invalid_analysis_provenance": 1
    }



def test_training_manifest_fails_closed_on_duplicate_replay_provenance(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "analysis"
    (analysis_root / "one").mkdir(parents=True)
    (analysis_root / "two").mkdir(parents=True)
    leaderboard_path = tmp_path / "leaderboard.json"
    leaderboard_path.write_text(
        json.dumps({"analysis_version": "state-pass-v4", "rows": []}),
        encoding="utf-8",
    )
    replay_sha = "b" * 64
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [{}, {}, {}, {}],
    }
    for directory in ("one", "two"):
        (analysis_root / directory / f"{replay_sha}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(
        ValueError,
        match=f"duplicate analysis provenance for replay {replay_sha}",
    ):
        build_training_manifest(
            analysis_root=analysis_root,
            leaderboard_path=leaderboard_path,
            raw_root=tmp_path / "raw",
        )



@pytest.mark.parametrize(
    ("modulus", "bucket"),
    [
        (0, 0),
        (1, 0),
        (True, 0),
        (10, -1),
        (10, 10),
        (10, True),
    ],
)
def test_training_manifest_rejects_invalid_holdout_partition_before_scan(
    tmp_path: Path,
    modulus: int,
    bucket: int,
) -> None:
    missing_leaderboard = tmp_path / "missing-leaderboard.json"

    with pytest.raises(ValueError):
        build_training_manifest(
            analysis_root=tmp_path / "analysis",
            leaderboard_path=missing_leaderboard,
            raw_root=tmp_path / "raw",
            holdout_modulus=modulus,
            holdout_bucket=bucket,
        )



@pytest.mark.parametrize(
    "mutated",
    [
        {"matches": "80"},
        {"matches": True},
        {"minutes": "500"},
        {"minutes": float("nan")},
        {"minutes": 10**400},
        {"rating": "56"},
        {"rating": float("inf")},
        {"rating": 10**400},
        {"rating_uncertainty": "0.8"},
        {"rating_uncertainty": float("nan")},
        {"rating_uncertainty": 10**400},
        {"player_id": ""},
        {"player_id": 123},
    ],
)
def test_select_players_rejects_malformed_leaderboard_evidence(
    mutated: dict,
) -> None:
    row = {
        "player_id": "name:alpha",
        "name": "Alpha",
        "role": "forward",
        "rating": 56.0,
        "rating_uncertainty": 0.8,
        "matches": 80,
        "minutes": 500.0,
    }
    row.update(mutated)

    assert select_players(
        [row],
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    ) == []


@pytest.mark.parametrize(
    "leaderboard",
    [
        [],
        {"analysis_version": "state-pass-v4"},
        {"analysis_version": "state-pass-v4", "rows": {}},
        {"analysis_version": "state-pass-v4", "rows": "not-a-list"},
    ],
)
def test_training_manifest_rejects_malformed_leaderboard_container(
    tmp_path: Path,
    leaderboard: object,
) -> None:
    leaderboard_path = tmp_path / "leaderboard.json"
    leaderboard_path.write_text(json.dumps(leaderboard), encoding="utf-8")

    with pytest.raises(ValueError):
        build_training_manifest(
            analysis_root=tmp_path / "analysis",
            leaderboard_path=leaderboard_path,
            raw_root=tmp_path / "raw",
        )



@pytest.mark.parametrize(
    "mutated",
    [
        {"schemaVersion": "4"},
        {"schemaVersion": True},
        {"totalFrames": "18000"},
        {"totalFrames": True},
        {"simulation": []},
        {"simulation": {"sampledStateCount": "1200"}},
        {"players": {}},
        {"players": [{}, {}, {}, "bad"]},
        {"featureSummary": []},
        {"featureSummary": {"touches": "100"}},
    ],
)
def test_replay_quality_rejects_coercible_analysis_evidence(
    mutated: dict,
) -> None:
    from haxlab.learning.selector import _replay_quality

    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [{}, {}, {}, {}],
    }
    payload.update(mutated)

    ok, reasons = _replay_quality(payload)

    assert ok is False
    assert reasons


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("teamId", "1"),
        ("teamId", True),
        ("samples", "900"),
        ("samples", True),
        ("id", "7"),
        ("id", True),
        ("id", -1),
    ],
)
def test_training_manifest_does_not_coerce_selected_player_evidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    leaderboard_path = tmp_path / "leaderboard.json"
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
    selected = {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900}
    selected[field] = value
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [
            selected,
            {"id": 8, "name": "Mate", "teamId": 1, "samples": 900},
            {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
            {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
        ],
    }
    (analysis_root / ("c" * 64 + ".json")).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=tmp_path / "raw",
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    )

    assert manifest["train_replays"] == []
    assert manifest["holdout_replays"] == []



@pytest.mark.parametrize(
    "name",
    [123, True, [], {}, object()],
)
def test_name_key_does_not_coerce_non_string_names(name: object) -> None:
    from haxlab.learning.selector import _name_key

    assert _name_key(name) is None


@pytest.mark.parametrize(
    "auth_hash",
    [123, True, [], {}, ""],
)
def test_identity_key_rejects_non_string_or_blank_auth_hash(
    auth_hash: object,
) -> None:
    from haxlab.learning.selector import _identity_key

    assert _identity_key({"authHash": auth_hash, "name": "Alpha"}) is None


@pytest.mark.parametrize(
    "mutated",
    [
        {"role": 1},
        {"role": True},
        {"role": ""},
        {"role": "   "},
    ],
)
def test_select_players_rejects_malformed_role_evidence(mutated: dict) -> None:
    row = {
        "player_id": "name:alpha",
        "name": "Alpha",
        "role": "forward",
        "rating": 56.0,
        "rating_uncertainty": 0.8,
        "matches": 80,
        "minutes": 500.0,
    }
    row.update(mutated)

    assert select_players(
        [row],
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=10.0,
    ) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"top_fraction_per_role": float("nan")},
        {"top_fraction_per_role": float("inf")},
        {"top_fraction_per_role": 10**400},
        {"top_fraction_per_role": -0.1},
        {"top_fraction_per_role": 1.1},
        {"top_fraction_per_role": True},
        {"min_players_per_role": 0},
        {"min_players_per_role": True},
        {"min_matches": 0},
        {"min_matches": True},
        {"min_minutes": float("nan")},
        {"min_minutes": 10**400},
        {"min_minutes": -1.0},
        {"max_uncertainty": float("inf")},
        {"max_uncertainty": 10**400},
        {"max_uncertainty": -1.0},
    ],
)
def test_select_players_rejects_invalid_selection_config(
    kwargs: dict,
) -> None:
    defaults = {
        "top_fraction_per_role": 1.0,
        "min_players_per_role": 1,
        "min_matches": 1,
        "min_minutes": 0.0,
        "max_uncertainty": 10.0,
    }
    defaults.update(kwargs)

    with pytest.raises(ValueError):
        select_players([], **defaults)



@pytest.mark.parametrize(
    "flag_value",
    [
        ("--min-players-per-role", "0"),
        ("--min-matches", "0"),
        ("--min-minutes", "-1"),
        ("--max-uncertainty", "-1"),
        ("--holdout-modulus", "1"),
        ("--holdout-bucket", "-1"),
        ("--top-fraction-per-role", "nan"),
    ],
)
def test_training_manifest_cli_rejects_invalid_values_without_clamping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag_value: tuple[str, str],
) -> None:
    from haxlab.learning.selector import main

    flag, value = flag_value
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-training-manifest",
            "--leaderboard",
            str(tmp_path / "missing.json"),
            flag,
            value,
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2



def test_training_manifest_rejects_symlinked_leaderboard(
    tmp_path: Path,
) -> None:
    target = tmp_path / "leaderboard-target.json"
    target.write_text(
        json.dumps({"analysis_version": "state-pass-v4", "rows": []}),
        encoding="utf-8",
    )
    link = tmp_path / "leaderboard.json"
    link.symlink_to(target)

    with pytest.raises(
        ValueError,
        match="leaderboard must be a regular non-symlink file",
    ):
        build_training_manifest(
            analysis_root=tmp_path / "analysis",
            leaderboard_path=link,
            raw_root=tmp_path / "raw",
        )


@pytest.mark.parametrize(
    "analysis_version",
    [None, "", "   ", 4, True, []],
)
def test_training_manifest_rejects_invalid_analysis_version(
    tmp_path: Path,
    analysis_version: object,
) -> None:
    leaderboard_path = tmp_path / "leaderboard.json"
    leaderboard_path.write_text(
        json.dumps({"analysis_version": analysis_version, "rows": []}),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="leaderboard analysis_version must be a non-empty string",
    ):
        build_training_manifest(
            analysis_root=tmp_path / "analysis",
            leaderboard_path=leaderboard_path,
            raw_root=tmp_path / "raw",
        )


def test_training_manifest_rejects_duplicate_valid_player_ids(
    tmp_path: Path,
) -> None:
    leaderboard_path = tmp_path / "leaderboard.json"
    row = {
        "player_id": "name:alpha",
        "name": "Alpha",
        "role": "forward",
        "rating": 56.0,
        "rating_uncertainty": 0.8,
        "matches": 80,
        "minutes": 500.0,
    }
    leaderboard_path.write_text(
        json.dumps(
            {
                "analysis_version": "state-pass-v4",
                "rows": [row, {**row, "role": "defender"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="leaderboard contains duplicate valid player_id rows",
    ):
        build_training_manifest(
            analysis_root=tmp_path / "analysis",
            leaderboard_path=leaderboard_path,
            raw_root=tmp_path / "raw",
            min_matches=1,
            min_minutes=0.0,
        )



def test_training_manifest_rejects_duplicate_selected_replay_player_id(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    leaderboard_path = tmp_path / "leaderboard.json"
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
                    },
                    {
                        "player_id": "name:beta",
                        "name": "Beta",
                        "role": "defender",
                        "rating": 55.0,
                        "rating_uncertainty": 0.7,
                        "matches": 70,
                        "minutes": 400.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    replay_sha = "d" * 64
    payload = {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "simulation": {"sampledStateCount": 1200},
        "featureSummary": {"touches": 100},
        "players": [
            {"id": 7, "name": "Alpha", "teamId": 1, "samples": 900},
            {"id": 7, "name": "Beta", "teamId": 1, "samples": 800},
            {"id": 9, "name": "Opp A", "teamId": 2, "samples": 900},
            {"id": 10, "name": "Opp B", "teamId": 2, "samples": 900},
        ],
    }
    (analysis_root / f"{replay_sha}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=f"{replay_sha}: duplicate selected replay_player_id",
    ):
        build_training_manifest(
            analysis_root=analysis_root,
            leaderboard_path=leaderboard_path,
            raw_root=tmp_path / "raw",
            top_fraction_per_role=1.0,
            min_players_per_role=1,
            min_matches=1,
            min_minutes=0.0,
            max_uncertainty=10.0,
        )



def test_atomic_training_manifest_publication_preserves_previous_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haxlab.learning import selector

    output = tmp_path / "manifest.json"
    previous = b'{"previous": true}\n'
    output.write_bytes(previous)

    original_dump = selector.json.dump

    def failing_dump(payload: object, handle: object, **kwargs: object) -> None:
        handle.write('{"partial":')
        raise OSError("simulated serialization failure")

    monkeypatch.setattr(selector.json, "dump", failing_dump)

    with pytest.raises(OSError, match="simulated serialization failure"):
        selector._atomic_json(output, {"next": True})

    assert output.read_bytes() == previous
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []

    monkeypatch.setattr(selector.json, "dump", original_dump)
    selector._atomic_json(output, {"next": True})
    assert json.loads(output.read_text(encoding="utf-8")) == {"next": True}
