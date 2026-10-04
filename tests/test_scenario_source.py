from __future__ import annotations

import sqlite3
from pathlib import Path

import haxlab.evaluation.scenario_source as scenario_source


def _db(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.executescript(
            """
            CREATE TABLE raw_replays (
                sha256 TEXT PRIMARY KEY,
                archive_path TEXT NOT NULL
            );
            CREATE TABLE replay_analysis_versions (
                sha256 TEXT NOT NULL,
                analyzer_version TEXT NOT NULL,
                status TEXT NOT NULL,
                output_path TEXT,
                sampled_state_count INTEGER,
                player_count INTEGER
            );
            """
        )
        for sha, samples in (("a" * 64, 2000), ("b" * 64, 1500)):
            db.execute(
                "INSERT INTO raw_replays (sha256, archive_path) VALUES (?, ?)",
                (sha, f"/raw/{sha}.hbr2"),
            )
            db.execute(
                """
                INSERT INTO replay_analysis_versions (
                    sha256, analyzer_version, status, output_path,
                    sampled_state_count, player_count
                ) VALUES (?, 'state-pass-v4', 'ok', ?, ?, 8)
                """,
                (sha, f"/analysis/{sha}.json", samples),
            )
        db.commit()
    finally:
        db.close()


def test_select_scenario_source_skips_excluded_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    def fake_healthy_candidate(**kwargs):
        return {
            "schema": "haxlab-replay-scenario-source-v1",
            "sha256": kwargs["sha256"],
        }

    monkeypatch.setattr(
        scenario_source,
        "_healthy_candidate",
        fake_healthy_candidate,
    )

    result = scenario_source.select_scenario_source(
        db_path,
        exclude_sha256={"A" * 64},
    )

    assert result["sha256"] == "b" * 64
    assert result["excluded_sha256_count"] == 1


def test_select_scenario_sources_returns_deterministic_disjoint_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    def fake_healthy_candidate(**kwargs):
        return {
            "schema": "haxlab-replay-scenario-source-v1",
            "sha256": kwargs["sha256"],
            "raw_path": kwargs["raw_path"],
            "analysis_path": kwargs["analysis_path"],
        }

    monkeypatch.setattr(
        scenario_source,
        "_healthy_candidate",
        fake_healthy_candidate,
    )

    result = scenario_source.select_scenario_sources(
        db_path,
        count=2,
    )

    assert result["schema"] == "haxlab-replay-scenario-source-set-v2"
    assert result["selection_algorithm"] == (
        "state-pass-v4-sampled-states-desc-sha256-asc"
    )
    assert result["source_count"] == 2
    assert [source["sha256"] for source in result["sources"]] == [
        "a" * 64,
        "b" * 64,
    ]
    assert len({source["sha256"] for source in result["sources"]}) == 2
    assert all(
        "excluded_sha256_count" not in source
        for source in result["sources"]
    )
