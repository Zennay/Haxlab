from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from haxlab.learning.manifest_audit import (
    AUDIT_SCHEMA,
    ManifestAuditError,
    audit_training_manifest,
)
from haxlab.learning.selector import build_training_manifest


REPLAY_SHA = "a" * 64


def _leaderboard(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-skill-leaderboard-v1",
                "generated_at": "2026-10-07T06:50:00+00:00",
                "analysis_version": "state-pass-v4",
                "source_root": "/var/lib/haxlab/derived/state-pass-v4",
                "min_matches": 1,
                "min_minutes": 0.0,
                "rows": [
                    {
                        "player_id": "auth:alpha",
                        "name": "Alpha",
                        "role": "midfield",
                        "rating": 60.0,
                        "rating_uncertainty": 1.0,
                        "matches": 30,
                        "minutes": 120.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _analysis(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 12000,
                "simulation": {"sampledStateCount": 2000},
                "featureSummary": {"touches": 20},
                "players": [
                    {
                        "id": 1,
                        "authHash": "alpha",
                        "name": "Alpha",
                        "teamId": 1,
                        "samples": 1000,
                    },
                    {
                        "id": 2,
                        "authHash": "beta",
                        "name": "Beta",
                        "teamId": 1,
                        "samples": 1000,
                    },
                    {
                        "id": 3,
                        "authHash": "gamma",
                        "name": "Gamma",
                        "teamId": 2,
                        "samples": 1000,
                    },
                    {
                        "id": 4,
                        "authHash": "delta",
                        "name": "Delta",
                        "teamId": 2,
                        "samples": 1000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _real_manifest(tmp_path: Path) -> tuple[Path, dict]:
    analysis_root = tmp_path / "state-pass-v4"
    analysis_root.mkdir()
    _analysis(analysis_root / f"{REPLAY_SHA}.json")

    leaderboard = tmp_path / "leaderboard.json"
    _leaderboard(leaderboard)
    raw_root = tmp_path / "raw"

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard,
        raw_root=raw_root,
        top_fraction_per_role=1.0,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=2.0,
        holdout_modulus=10,
        holdout_bucket=0,
    )
    output = tmp_path / "manifest.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output, manifest


def _write(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_audit_accepts_real_training_manifest_and_is_deterministic(
    tmp_path: Path,
) -> None:
    path, _ = _real_manifest(tmp_path)

    first = audit_training_manifest(path)
    second = audit_training_manifest(path)

    assert first == second
    assert first["schema"] == AUDIT_SCHEMA
    assert first["ok"] is True
    assert first["analysis_version"] == "state-pass-v4"
    assert first["selected_player_count"] == 1
    assert first["train_replay_count"] + first["holdout_replay_count"] == 1
    assert len(first["sha256"]) == 64
    assert len(first["inventory_sha256"]) == 64


def test_audit_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema":"haxlab-human-imitation-manifest-v3",'
        '"schema":"haxlab-human-imitation-manifest-v3"}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestAuditError, match="duplicate JSON object key"):
        audit_training_manifest(path)


def test_audit_rejects_duplicate_selected_player_identity(tmp_path: Path) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    broken["selected_players"].append(copy.deepcopy(broken["selected_players"][0]))
    broken["stats"]["selected_player_count"] = 2
    _write(path, broken)

    with pytest.raises(ManifestAuditError, match="duplicate selected player_id"):
        audit_training_manifest(path)


def test_audit_rejects_stats_count_drift(tmp_path: Path) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    broken["stats"]["train_replay_count"] += 1
    _write(path, broken)

    with pytest.raises(ManifestAuditError, match="train_replay_count"):
        audit_training_manifest(path)


def test_audit_rejects_deterministic_split_drift(tmp_path: Path) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    if broken["train_replays"]:
        replay = broken["train_replays"].pop()
        broken["holdout_replays"].append(replay)
    else:
        replay = broken["holdout_replays"].pop()
        broken["train_replays"].append(replay)
    broken["stats"]["train_replay_count"] = len(broken["train_replays"])
    broken["stats"]["holdout_replay_count"] = len(broken["holdout_replays"])
    _write(path, broken)

    with pytest.raises(ManifestAuditError, match="holdout bucket"):
        audit_training_manifest(path)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("selection", "holdout_modulus", True),
        ("selection", "top_fraction_per_role", float("nan")),
        ("selection", "max_uncertainty", -1.0),
        ("stats", "analysis_files_scanned", 1.5),
    ],
)
def test_audit_rejects_malformed_native_numeric_evidence(
    tmp_path: Path,
    section: str,
    field: str,
    value: object,
) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    broken[section][field] = value
    _write(path, broken)

    with pytest.raises(ManifestAuditError):
        audit_training_manifest(path)


def test_audit_rejects_raw_provenance_path_drift(tmp_path: Path) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    rows = broken["train_replays"] or broken["holdout_replays"]
    rows[0]["raw_path"] = str(tmp_path / "wrong.hbr2")
    _write(path, broken)

    with pytest.raises(ManifestAuditError, match="canonical raw provenance"):
        audit_training_manifest(path)


def test_audit_rejects_selected_player_evidence_drift(tmp_path: Path) -> None:
    path, manifest = _real_manifest(tmp_path)
    broken = copy.deepcopy(manifest)
    rows = broken["train_replays"] or broken["holdout_replays"]
    rows[0]["selected_player_ids"] = ["auth:other"]
    _write(path, broken)

    with pytest.raises(ManifestAuditError, match="unselected identity"):
        audit_training_manifest(path)


def test_audit_rejects_symlinked_manifest(tmp_path: Path) -> None:
    target, _ = _real_manifest(tmp_path)
    link = tmp_path / "manifest-link.json"
    link.symlink_to(target)

    with pytest.raises(ManifestAuditError, match="regular non-symlink"):
        audit_training_manifest(link)
