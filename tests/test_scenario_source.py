from __future__ import annotations

import hashlib
import json
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


def _candidate_files(tmp_path: Path) -> tuple[Path, Path, str]:
    raw = tmp_path / "replay.hbr2"
    raw.write_bytes(b"haxlab-frozen-replay-bytes")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()

    players = [
        {"id": index + 1, "teamId": 1 if index < 4 else 2, "samples": 100}
        for index in range(8)
    ]
    analysis = tmp_path / "analysis.json"
    analysis.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 10800,
                "featureSummary": {"touches": 100},
                "simulation": {"sampledStateCount": 100},
                "players": players,
            }
        ),
        encoding="utf-8",
    )
    return raw, analysis, raw_sha


def _fake_roles(players):
    roles = ("gk", "dm", "am", "st")
    return {
        int(player["id"]): {
            "role": roles[index % 4],
            "confidence": 0.9,
        }
        for index, player in enumerate(players)
    }


def test_healthy_candidate_verifies_raw_replay_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is not None
    assert candidate["sha256"] == raw_sha
    assert candidate["raw_file_sha256_verified"] is True
    assert candidate["sampled_states"] == 100


def test_healthy_candidate_rejects_raw_sha_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, _ = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    candidate = scenario_source._healthy_candidate(
        sha256="a" * 64,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_sample_count_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=99,
    )

    assert candidate is None



def test_healthy_candidate_rejects_non_object_analysis(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    analysis.write_text(json.dumps([]), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_malformed_nested_objects(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["featureSummary"] = []
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_non_native_integer_metrics(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["totalFrames"] = "10800"
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_malformed_player_rows(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["players"][0]["samples"] = "100"
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_duplicate_player_ids(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["players"][1]["id"] = payload["players"][0]["id"]
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_malformed_simulation(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["simulation"] = ["not", "an", "object"]
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_selector_skips_corrupt_db_sample_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            "UPDATE replay_analysis_versions "
            "SET sampled_state_count = 'broken' WHERE sha256 = ?",
            ("a" * 64,),
        )
        db.commit()
    finally:
        db.close()

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

    result = scenario_source.select_scenario_source(db_path)

    assert result["sha256"] == "b" * 64



def test_healthy_candidate_rejects_boolean_player_integer(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["players"][0]["teamId"] = False
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_healthy_candidate_rejects_boolean_sample_count(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["simulation"]["sampledStateCount"] = False
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None


def test_single_source_selector_requires_strict_positive_max_candidates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    for value in (False, True, 0, -1, 1.0, "1"):
        with pytest.raises(ValueError, match="max_candidates must"):
            scenario_source.select_scenario_source(
                db_path,
                max_candidates=value,  # type: ignore[arg-type]
            )


def test_multisource_selector_requires_strict_positive_count(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    for value in (False, True, 0, -1, 2.0, "2"):
        with pytest.raises(ValueError, match="count must"):
            scenario_source.select_scenario_sources(
                db_path,
                count=value,  # type: ignore[arg-type]
            )


def test_multisource_selector_requires_strict_positive_max_candidates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    for value in (False, True, 0, -1, 1000.0, "1000"):
        with pytest.raises(ValueError, match="max_candidates must"):
            scenario_source.select_scenario_sources(
                db_path,
                count=1,
                max_candidates=value,  # type: ignore[arg-type]
            )


def test_selector_preserves_valid_explicit_limits(
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

    single = scenario_source.select_scenario_source(
        db_path,
        max_candidates=1,
    )
    multi = scenario_source.select_scenario_sources(
        db_path,
        count=1,
        max_candidates=1,
    )

    assert single["sha256"] == "a" * 64
    assert multi["requested_source_count"] == 1
    assert multi["source_count"] == 1
    assert multi["sources"][0]["sha256"] == "a" * 64
