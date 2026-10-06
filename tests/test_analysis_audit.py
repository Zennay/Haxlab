from __future__ import annotations

import json
import sys
from pathlib import Path

from haxlab.runtime.analysis_audit import AUDIT_SCHEMA, audit_analysis_artifacts, main
from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


def _artifact_payload(
    *,
    total_frames: int = 600,
    sampled_states: int = 100,
    raw_events: int = 50,
    players: int = 2,
) -> dict[str, object]:
    return {
        "schemaVersion": 4,
        "totalFrames": total_frames,
        "rawEventCount": raw_events,
        "players": [{"id": index} for index in range(players)],
        "simulation": {
            "sampleEveryTicks": 6,
            "sampledStateCount": sampled_states,
            "framesAdvanced": total_frames,
        },
    }


def _register_ok_analysis(
    state: RuntimeState,
    *,
    derived_root: Path,
    sha256: str,
    payload: dict[str, object],
) -> Path:
    replay = derived_root.parent / f"{sha256[:8]}.hbr2"
    replay.write_bytes(b"x")
    output = (
        derived_root
        / CURRENT_ANALYZER_VERSION
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload), encoding="utf-8")

    simulation = payload["simulation"]
    assert isinstance(simulation, dict)
    players = payload["players"]
    assert isinstance(players, list)

    state.register_raw(
        sha256=sha256,
        archive_path=str(replay),
        size_bytes=1,
    )
    state.mark_replay_processing(
        sha256=sha256,
        status="ok",
        format_version=3,
        total_frames=int(payload["totalFrames"]),
        duration_seconds=10.0,
        decompressed_bytes=10,
    )
    state.mark_replay_analysis(
        sha256=sha256,
        analyzer_version=CURRENT_ANALYZER_VERSION,
        status="ok",
        output_path=str(output),
        sampled_state_count=int(simulation["sampledStateCount"]),
        player_count=len(players),
        raw_event_count=int(payload["rawEventCount"]),
        tick_count=int(simulation["framesAdvanced"]),
    )
    return output


def test_analysis_artifact_audit_accepts_exact_ledger_match(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    sha = "a" * 64

    with RuntimeState(db) as state:
        _register_ok_analysis(
            state,
            derived_root=derived,
            sha256=sha,
            payload=_artifact_payload(),
        )
        report = audit_analysis_artifacts(state, derived_root=derived)

    assert report["schema"] == AUDIT_SCHEMA
    assert report["ok"] is True
    assert report["checked_records"] == 1
    assert report["valid_objects"] == 1
    assert report["existing_files"] == 1
    assert report["objects_with_issues"] == 0
    assert report["issues"] == []


def test_analysis_artifact_audit_rejects_tampered_payload(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    sha = "b" * 64

    with RuntimeState(db) as state:
        output = _register_ok_analysis(
            state,
            derived_root=derived,
            sha256=sha,
            payload=_artifact_payload(),
        )
        tampered = _artifact_payload(raw_events=51)
        output.write_text(json.dumps(tampered), encoding="utf-8")

        report = audit_analysis_artifacts(state, derived_root=derived)

    assert report["ok"] is False
    assert report["payload_mismatches"] == 1
    assert report["objects_with_issues"] == 1
    assert report["issues_truncated"] is False
    reasons = report["issues"][0]["reasons"]
    assert reasons == ["raw_event_count_mismatch:expected=50:actual=51"]


def test_analysis_artifact_audit_rejects_missing_and_misplaced_outputs(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"

    with RuntimeState(db) as state:
        missing_sha = "c" * 64
        missing = _register_ok_analysis(
            state,
            derived_root=derived,
            sha256=missing_sha,
            payload=_artifact_payload(),
        )
        missing.unlink()

        misplaced_sha = "d" * 64
        expected = _register_ok_analysis(
            state,
            derived_root=derived,
            sha256=misplaced_sha,
            payload=_artifact_payload(),
        )
        misplaced = derived / "misplaced.json"
        expected.replace(misplaced)
        state.mark_replay_analysis(
            sha256=misplaced_sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(misplaced),
            sampled_state_count=100,
            player_count=2,
            raw_event_count=50,
            tick_count=600,
        )

        report = audit_analysis_artifacts(state, derived_root=derived)

    assert report["ok"] is False
    assert report["checked_records"] == 2
    assert report["missing_files"] == 1
    assert report["path_mismatches"] == 1
    assert report["objects_with_issues"] == 2


def test_analysis_artifact_audit_cli_fails_closed_and_truncates_details(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    sha = "e" * 64

    with RuntimeState(db) as state:
        output = _register_ok_analysis(
            state,
            derived_root=derived,
            sha256=sha,
            payload=_artifact_payload(),
        )
        output.write_text("{", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-audit-analysis",
            "--state-db",
            str(db),
            "--derived-root",
            str(derived),
            "--max-issues",
            "0",
        ],
    )
    assert main() == 2
    report = json.loads(capsys.readouterr().out)

    assert report["ok"] is False
    assert report["invalid_json"] == 1
    assert report["objects_with_issues"] == 1
    assert report["issues"] == []
    assert report["issues_truncated"] is True
