from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import haxlab.skill.leaderboard_audit as audit_module

from haxlab.skill.leaderboard import main as leaderboard_main
from haxlab.skill.leaderboard_audit import (
    AUDIT_SCHEMA,
    DIMENSIONS,
    LeaderboardAuditError,
    audit_leaderboard,
)


def _dimension(mean: float = 0.0) -> dict:
    return {
        "mean": mean,
        "uncertainty": 0.25,
        "effective_weight": 12.0,
    }


def _row(
    player_id: str,
    *,
    name: str,
    rating: float,
    uncertainty: float = 2.5,
    matches: int = 20,
    minutes: float = 60.0,
) -> dict:
    overall_z = (rating - 50.0) / 10.0
    return {
        "player_id": player_id,
        "name": name,
        "matches": matches,
        "minutes": minutes,
        "role": "midfield",
        "rating": rating,
        "rating_uncertainty": uncertainty,
        "overall_z": overall_z,
        "average_teammate_context": 0.1,
        "average_opponent_context": -0.1,
        "dimensions": {name: _dimension() for name in DIMENSIONS},
    }


def _snapshot(*rows: dict, analysis_version: str | None = None) -> dict:
    payload = {
        "schema": "haxlab-skill-leaderboard-v1",
        "generated_at": "2026-10-07T06:40:00+00:00",
        "source_root": "/var/lib/haxlab/derived/state-pass-v4",
        "min_matches": 20,
        "min_minutes": 60.0,
        "rows": list(rows),
    }
    if analysis_version is not None:
        payload["analysis_version"] = analysis_version
    return payload


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_audit_accepts_current_v1_and_emits_deterministic_inventory(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    payload = _snapshot(
        _row("auth:alpha", name="Alpha", rating=62.0),
        _row("auth:beta", name="Beta", rating=55.0),
    )
    _write(path, payload)

    first = audit_leaderboard(path)
    second = audit_leaderboard(path)

    assert first == second
    assert first["schema"] == AUDIT_SCHEMA
    assert first["ok"] is True
    assert first["row_count"] == 2
    assert first["analysis_version"] is None
    assert len(first["sha256"]) == 64
    assert len(first["inventory_sha256"]) == 64


def test_audit_accepts_and_validates_optional_analysis_version(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(
            _row("auth:alpha", name="Alpha", rating=60.0),
            analysis_version="state-pass-v4",
        ),
    )

    receipt = audit_leaderboard(path)

    assert receipt["analysis_version"] == "state-pass-v4"


@pytest.mark.parametrize("analysis_version", ["", " state-pass-v4", "state/pass-v4"])
def test_audit_rejects_noncanonical_analysis_version(
    tmp_path: Path,
    analysis_version: str,
) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(
            _row("auth:alpha", name="Alpha", rating=60.0),
            analysis_version=analysis_version,
        ),
    )

    with pytest.raises(LeaderboardAuditError, match="analysis_version"):
        audit_leaderboard(path)


def test_audit_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    path.write_text(
        '{"schema":"haxlab-skill-leaderboard-v1",'
        '"schema":"haxlab-skill-leaderboard-v1"}',
        encoding="utf-8",
    )

    with pytest.raises(LeaderboardAuditError, match="duplicate JSON object key"):
        audit_leaderboard(path)


def test_audit_rejects_duplicate_player_id(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(
            _row("auth:same", name="Alpha", rating=62.0),
            _row("auth:same", name="Alpha Two", rating=55.0),
        ),
    )

    with pytest.raises(LeaderboardAuditError, match="duplicate player_id"):
        audit_leaderboard(path)


def test_audit_rejects_threshold_incompatible_row(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(
            _row(
                "auth:alpha",
                name="Alpha",
                rating=60.0,
                matches=19,
                minutes=60.0,
            )
        ),
    )

    with pytest.raises(LeaderboardAuditError, match="below snapshot min_matches"):
        audit_leaderboard(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rating", True),
        ("rating_uncertainty", -0.1),
        ("average_teammate_context", 1.1),
        ("overall_z", float("nan")),
    ],
)
def test_audit_rejects_malformed_numeric_evidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / "leaderboard.json"
    row = _row("auth:alpha", name="Alpha", rating=60.0)
    row[field] = value
    _write(path, _snapshot(row))

    with pytest.raises(LeaderboardAuditError):
        audit_leaderboard(path)


def test_audit_rejects_noncanonical_row_order(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(
            _row("auth:beta", name="Beta", rating=55.0),
            _row("auth:alpha", name="Alpha", rating=62.0),
        ),
    )

    with pytest.raises(LeaderboardAuditError, match="canonical rating ordering"):
        audit_leaderboard(path)


def test_audit_rejects_dimension_schema_drift(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    row = _row("auth:alpha", name="Alpha", rating=60.0)
    del row["dimensions"]["defending"]
    _write(path, _snapshot(row))

    with pytest.raises(LeaderboardAuditError, match="dimensions must contain exactly"):
        audit_leaderboard(path)


def test_audit_rejects_rating_overall_z_drift(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    row = _row("auth:alpha", name="Alpha", rating=60.0)
    row["overall_z"] = 0.5
    _write(path, _snapshot(row))

    with pytest.raises(LeaderboardAuditError, match="inconsistent with overall_z"):
        audit_leaderboard(path)


def test_audit_rejects_symlinked_leaderboard(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    link = tmp_path / "leaderboard.json"
    _write(target, _snapshot(_row("auth:alpha", name="Alpha", rating=60.0)))
    link.symlink_to(target)

    with pytest.raises(LeaderboardAuditError, match="regular non-symlink"):
        audit_leaderboard(link)


def test_audit_handles_short_regular_file_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "leaderboard.json"
    _write(path, _snapshot(_row("auth:alpha", name="Alpha", rating=60.0)))

    real_read = audit_module.os.read

    def short_read(fd: int, size: int) -> bytes:
        return real_read(fd, min(size, 7))

    monkeypatch.setattr(audit_module.os, "read", short_read)

    receipt = audit_leaderboard(path)

    assert receipt["ok"] is True
    assert receipt["row_count"] == 1


def test_audit_accepts_real_producer_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    analysis_root = tmp_path / "state-pass-v4"
    analysis_root.mkdir()
    (analysis_root / "match.json").write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "totalFrames": 21600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [
                    {
                        "id": 1,
                        "name": "Alpha",
                        "teamId": 1,
                        "samples": 3600,
                        "averageX": 0.0,
                        "averageY": 0.0,
                        "nearestBallSamples": 1000,
                        "closeBallSamples": 400,
                        "kickEvents": 20,
                        "inferredRetainedChains": 12,
                        "inferredLostChains": 3,
                        "inferredRecoveries": 5,
                        "inferredGoals": 1,
                        "inferredAssists": 1,
                        "progressionEvents": 8,
                        "progressionSum": 80.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "leaderboard.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-skill",
            "--root",
            str(analysis_root),
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

    assert leaderboard_main() == 0
    capsys.readouterr()

    receipt = audit_leaderboard(output)

    assert receipt["ok"] is True
    assert receipt["row_count"] == 1


def test_audit_cli_failure_is_machine_readable(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    path = tmp_path / "leaderboard.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["haxlab-skill-leaderboard-audit", str(path)])

    assert audit_module.main() == 2
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert captured.out == ""
    assert error["schema"] == AUDIT_SCHEMA
    assert error["ok"] is False
    assert error["error"]



def test_audit_rejects_logical_path_replacement_after_secure_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "leaderboard.json"
    _write(
        path,
        _snapshot(_row("auth:alpha", name="Alpha", rating=60.0)),
    )
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(path.read_bytes())
    original = tmp_path / "leaderboard-original.json"

    real_read = audit_module._read_bounded
    reads = 0

    def racing_read(fd: int) -> bytes:
        nonlocal reads
        payload = real_read(fd)
        reads += 1
        if reads == 2:
            path.rename(original)
            replacement.rename(path)
        return payload

    monkeypatch.setattr(audit_module, "_read_bounded", racing_read)

    with pytest.raises(
        LeaderboardAuditError,
        match="logical path identity changed during audit",
    ):
        audit_leaderboard(path)

    assert reads == 2
