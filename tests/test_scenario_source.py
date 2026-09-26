from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.evaluation import scenario_source


def _analysis_payload(player_keys: list[str]) -> dict:
    players = []
    xs = [-180.0, -60.0, 60.0, 180.0]
    player_id = 1
    for team_id in (1, 2):
        for index, attack_x in enumerate(xs):
            world_x = attack_x if team_id == 1 else -attack_x
            key = player_keys[player_id - 1]
            players.append(
                {
                    "id": player_id,
                    "name": f"p{player_id}",
                    "authHash": key,
                    "teamId": team_id,
                    "samples": 100,
                    "averageX": world_x,
                }
            )
            player_id += 1
    return {
        "schemaVersion": 4,
        "totalFrames": 18000,
        "featureSummary": {"touches": 250},
        "simulation": {"sampledStateCount": 100},
        "players": players,
    }


def _candidate(
    tmp_path: Path,
    name: str,
    player_keys: list[str],
) -> tuple[str, str, str, int]:
    raw = tmp_path / f"{name}.hbr2"
    analysis = tmp_path / f"{name}.json"
    raw.write_bytes(b"HBR2")
    analysis.write_text(
        json.dumps(_analysis_payload(player_keys)),
        encoding="utf-8",
    )
    return name * 64 if len(name) == 1 else name.ljust(64, "0"), str(raw), str(analysis), 100


def test_selects_diverse_4v4_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_keys = [f"a{i}" for i in range(8)]
    overlapping_keys = first_keys[:5] + [f"b{i}" for i in range(3)]
    distinct_keys = [f"c{i}" for i in range(8)]

    rows = [
        _candidate(tmp_path, "a", first_keys),
        _candidate(tmp_path, "b", overlapping_keys),
        _candidate(tmp_path, "c", distinct_keys),
    ]
    monkeypatch.setattr(
        scenario_source,
        "_candidate_rows",
        lambda db_path, max_candidates: rows,
    )

    selected = scenario_source.select_scenario_sources(
        tmp_path / "unused.sqlite3",
        count=2,
        max_candidates=100,
        max_shared_players=4,
    )

    assert [row["sha256"][0] for row in selected] == ["a", "c"]
    assert len(selected[0]["core_player_keys"]) == 8
    assert len(
        set(selected[0]["core_player_keys"])
        & set(selected[1]["core_player_keys"])
    ) == 0


def test_diverse_selector_fails_closed_when_pool_is_too_similar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_keys = [f"a{i}" for i in range(8)]
    overlapping_keys = first_keys[:7] + ["other"]

    rows = [
        _candidate(tmp_path, "a", first_keys),
        _candidate(tmp_path, "b", overlapping_keys),
    ]
    monkeypatch.setattr(
        scenario_source,
        "_candidate_rows",
        lambda db_path, max_candidates: rows,
    )

    with pytest.raises(RuntimeError, match="Could not find enough diverse"):
        scenario_source.select_scenario_sources(
            tmp_path / "unused.sqlite3",
            count=2,
            max_candidates=100,
            max_shared_players=4,
        )



def test_selector_excludes_explicit_replay_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_keys = [f"a{i}" for i in range(8)]
    second_keys = [f"b{i}" for i in range(8)]
    rows = [
        _candidate(tmp_path, "a", first_keys),
        _candidate(tmp_path, "b", second_keys),
    ]
    monkeypatch.setattr(
        scenario_source,
        "_candidate_rows",
        lambda db_path, max_candidates: rows,
    )

    selected = scenario_source.select_scenario_sources(
        tmp_path / "unused.sqlite3",
        count=1,
        max_candidates=100,
        max_shared_players=8,
        exclude_sha256s={rows[0][0]},
    )

    assert len(selected) == 1
    assert selected[0]["sha256"] == rows[1][0]
