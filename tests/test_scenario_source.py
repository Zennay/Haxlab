from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("bad_value", [True, 1.5, "2", 0, -1])
def test_select_scenario_source_rejects_invalid_max_candidates(
    tmp_path: Path,
    bad_value,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    with pytest.raises(
        ValueError,
        match="max_candidates must be a native positive integer",
    ):
        scenario_source.select_scenario_source(
            db_path,
            max_candidates=bad_value,
        )


@pytest.mark.parametrize("bad_value", [True, 1.5, "2", 0, -1])
def test_select_scenario_sources_rejects_invalid_count(
    tmp_path: Path,
    bad_value,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    with pytest.raises(
        ValueError,
        match="count must be a native positive integer",
    ):
        scenario_source.select_scenario_sources(
            db_path,
            count=bad_value,
        )


@pytest.mark.parametrize(
    "bad_value",
    ["not-a-sha", "a" * 63, "g" * 64, 123],
)
def test_selector_rejects_malformed_exclusion_provenance(
    tmp_path: Path,
    bad_value,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    with pytest.raises(ValueError, match="exclude_sha256"):
        scenario_source.select_scenario_source(
            db_path,
            exclude_sha256={bad_value},  # type: ignore[arg-type]
        )


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
    payload_bytes = b"haxlab-frozen-replay-bytes"
    raw_sha = hashlib.sha256(payload_bytes).hexdigest()
    raw = tmp_path / f"{raw_sha}.hbr2"
    raw.write_bytes(payload_bytes)

    players = [
        {"id": index + 1, "teamId": 1 if index < 4 else 2, "samples": 100}
        for index in range(8)
    ]
    analysis = tmp_path / f"{raw_sha}.json"
    analysis.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "sourceFile": raw.name,
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
    assert candidate["analysis_sha256"] == hashlib.sha256(
        analysis.read_bytes()
    ).hexdigest()
    assert candidate["sampled_states"] == 100


def test_healthy_candidate_analysis_fingerprint_tracks_exact_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    first = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )
    assert first is not None

    payload = json.loads(analysis.read_text(encoding="utf-8"))
    analysis.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    second = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )
    assert second is not None
    assert second["analysis_sha256"] == hashlib.sha256(
        analysis.read_bytes()
    ).hexdigest()
    assert second["analysis_sha256"] != first["analysis_sha256"]


def test_healthy_candidate_rejects_symlinked_source_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    raw_link = tmp_path / "raw-link.hbr2"
    raw_link.symlink_to(raw)
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw_link),
            analysis_path=str(analysis),
            sampled_states=100,
        )
        is None
    )

    analysis_link = tmp_path / "analysis-link.json"
    analysis_link.symlink_to(analysis)
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw),
            analysis_path=str(analysis_link),
            sampled_states=100,
        )
        is None
    )


def test_healthy_candidate_rejects_non_file_source_artifacts(
    tmp_path: Path,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(directory),
            analysis_path=str(analysis),
            sampled_states=100,
        )
        is None
    )
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw),
            analysis_path=str(directory),
            sampled_states=100,
        )
        is None
    )


def test_healthy_candidate_rejects_malformed_role_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)

    def bad_role_type(players):
        rows = _fake_roles(players)
        rows[1]["role"] = 123
        return rows

    monkeypatch.setattr(scenario_source, "infer_roles_4v4", bad_role_type)
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw),
            analysis_path=str(analysis),
            sampled_states=100,
        )
        is None
    )

    def impossible_confidence(players):
        rows = _fake_roles(players)
        rows[1]["confidence"] = 1.01
        return rows

    monkeypatch.setattr(
        scenario_source,
        "infer_roles_4v4",
        impossible_confidence,
    )
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw),
            analysis_path=str(analysis),
            sampled_states=100,
        )
        is None
    )


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

@pytest.mark.parametrize("bad_value", [True, 1.5, "2", 0, -1])
def test_select_scenario_sources_rejects_invalid_max_candidates(
    tmp_path: Path,
    bad_value,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)

    with pytest.raises(
        ValueError,
        match="max_candidates must be a native positive integer",
    ):
        scenario_source.select_scenario_sources(
            db_path,
            count=1,
            max_candidates=bad_value,
        )


def test_selector_skips_malformed_database_replay_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    _db(db_path)
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            "INSERT INTO raw_replays (sha256, archive_path) VALUES (?, ?)",
            ("not-a-sha", "/raw/not-a-sha.hbr2"),
        )
        db.execute(
            """
            INSERT INTO replay_analysis_versions (
                sha256, analyzer_version, status, output_path,
                sampled_state_count, player_count
            ) VALUES (?, 'state-pass-v4', 'ok', ?, ?, 8)
            """,
            ("not-a-sha", "/analysis/not-a-sha.json", 3000),
        )
        db.commit()
    finally:
        db.close()

    seen: list[str] = []

    def fake_healthy_candidate(**kwargs):
        seen.append(kwargs["sha256"])
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

    assert result["sha256"] == "a" * 64
    assert "not-a-sha" not in seen

@pytest.mark.parametrize("bad_source_file", ["other-replay.hbr2", 123, None])
def test_healthy_candidate_rejects_analysis_bound_to_other_source(
    tmp_path: Path,
    bad_source_file,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    payload["sourceFile"] = bad_source_file
    analysis.write_text(json.dumps(payload), encoding="utf-8")

    candidate = scenario_source._healthy_candidate(
        sha256=raw_sha,
        raw_path=str(raw),
        analysis_path=str(analysis),
        sampled_states=100,
    )

    assert candidate is None

def test_healthy_candidate_rejects_noncanonical_source_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw, analysis, raw_sha = _candidate_files(tmp_path)
    monkeypatch.setattr(scenario_source, "infer_roles_4v4", _fake_roles)

    wrong_raw = tmp_path / "renamed.hbr2"
    wrong_raw.write_bytes(raw.read_bytes())
    wrong_analysis = tmp_path / "renamed.json"
    wrong_analysis.write_bytes(analysis.read_bytes())

    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(wrong_raw),
            analysis_path=str(analysis),
            sampled_states=100,
        )
        is None
    )
    assert (
        scenario_source._healthy_candidate(
            sha256=raw_sha,
            raw_path=str(raw),
            analysis_path=str(wrong_analysis),
            sampled_states=100,
        )
        is None
    )

