from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from haxlab.learning import manifest_source_audit
from haxlab.learning.manifest_source_audit import (
    AUDIT_SCHEMA,
    ManifestSourceAuditError,
    audit_manifest_sources,
)
from haxlab.learning.selector import build_training_manifest
from haxlab.skill.leaderboard_audit import DIMENSIONS


REPLAY_SHA = "a" * 64


def _leaderboard_row(
    player_id: str,
    name: str,
    *,
    rating: float,
) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "matches": 30,
        "minutes": 120.0,
        "role": "midfield",
        "rating": rating,
        "rating_uncertainty": 1.0,
        "overall_z": (rating - 50.0) / 10.0,
        "average_teammate_context": 0.0,
        "average_opponent_context": 0.0,
        "dimensions": {
            dimension: {
                "mean": 0.0,
                "uncertainty": 0.25,
                "effective_weight": 12.0,
            }
            for dimension in DIMENSIONS
        },
    }


def _write_leaderboard(path: Path, analysis_root: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-skill-leaderboard-v1",
                "generated_at": "2026-10-07T06:55:00+00:00",
                "analysis_version": analysis_root.name,
                "source_root": str(analysis_root),
                "min_matches": 1,
                "min_minutes": 0.0,
                "rows": [
                    _leaderboard_row("auth:alpha", "Alpha", rating=60.0),
                    _leaderboard_row("auth:beta", "Beta", rating=55.0),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _analysis_payload() -> dict:
    return {
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


def _fixture(tmp_path: Path) -> tuple[Path, dict, Path, Path, Path]:
    analysis_root = tmp_path / "state-pass-v4"
    analysis_root.mkdir()
    analysis_path = analysis_root / f"{REPLAY_SHA}.json"
    analysis_path.write_text(
        json.dumps(_analysis_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    leaderboard_path = tmp_path / "leaderboard.json"
    _write_leaderboard(leaderboard_path, analysis_root)

    raw_root = tmp_path / "raw"
    raw_path = raw_root / REPLAY_SHA[:2] / REPLAY_SHA[2:4] / f"{REPLAY_SHA}.hbr2"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"HBR2-test-source")

    manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=raw_root,
        top_fraction_per_role=0.5,
        min_players_per_role=1,
        min_matches=1,
        min_minutes=0.0,
        max_uncertainty=2.0,
        holdout_modulus=10,
        holdout_bucket=0,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path, manifest, leaderboard_path, analysis_path, raw_path


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _replay_row(manifest: dict) -> dict:
    rows = manifest["train_replays"] or manifest["holdout_replays"]
    assert len(rows) == 1
    return rows[0]


def test_source_audit_accepts_real_manifest_and_is_deterministic(
    tmp_path: Path,
) -> None:
    manifest_path, _, _, _, _ = _fixture(tmp_path)

    first = audit_manifest_sources(manifest_path)
    second = audit_manifest_sources(manifest_path)

    assert first == second
    assert first["schema"] == AUDIT_SCHEMA
    assert first["ok"] is True
    assert first["analysis_artifact_count"] == 1
    assert first["raw_artifact_count"] == 1
    assert len(first["source_inventory_sha256"]) == 64


def test_source_audit_rejects_linked_leaderboard_tamper(tmp_path: Path) -> None:
    manifest_path, _, leaderboard_path, _, _ = _fixture(tmp_path)
    leaderboard_path.write_text(
        leaderboard_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ManifestSourceAuditError,
        match="leaderboard SHA-256 does not match manifest",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_linked_analysis_tamper(tmp_path: Path) -> None:
    manifest_path, _, _, analysis_path, _ = _fixture(tmp_path)
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    payload["featureSummary"]["touches"] = 21
    analysis_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ManifestSourceAuditError,
        match="analysis SHA-256 does not match manifest",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_selection_policy_drift(tmp_path: Path) -> None:
    manifest_path, manifest, _, _, _ = _fixture(tmp_path)
    broken = copy.deepcopy(manifest)
    broken["selection"]["top_fraction_per_role"] = 1.0
    _write_manifest(manifest_path, broken)

    with pytest.raises(
        ManifestSourceAuditError,
        match="selected_players does not match linked leaderboard policy",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_rehashed_analysis_player_drift(
    tmp_path: Path,
) -> None:
    manifest_path, manifest, _, analysis_path, _ = _fixture(tmp_path)
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    payload["players"][0]["samples"] = 999
    new_bytes = json.dumps(payload).encode("utf-8")
    analysis_path.write_bytes(new_bytes)

    broken = copy.deepcopy(manifest)
    replay = _replay_row(broken)
    replay["analysis_size_bytes"] = len(new_bytes)
    replay["analysis_sha256"] = hashlib.sha256(new_bytes).hexdigest()
    _write_manifest(manifest_path, broken)

    with pytest.raises(
        ManifestSourceAuditError,
        match="selected_players does not match linked analysis evidence",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_missing_raw_replay(tmp_path: Path) -> None:
    manifest_path, _, _, _, raw_path = _fixture(tmp_path)
    raw_path.unlink()

    with pytest.raises(ManifestSourceAuditError, match="raw replay is not readable"):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_symlinked_raw_replay(tmp_path: Path) -> None:
    manifest_path, _, _, _, raw_path = _fixture(tmp_path)
    target = tmp_path / "raw-target.hbr2"
    target.write_bytes(raw_path.read_bytes())
    raw_path.unlink()
    raw_path.symlink_to(target)

    with pytest.raises(
        ManifestSourceAuditError,
        match="raw replay must be a regular non-symlink file",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_byte_identical_manifest_handoff_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _, _, _, _ = _fixture(tmp_path)
    replacement = tmp_path / "replacement-manifest.json"
    original = tmp_path / "original-manifest.json"
    replacement.write_bytes(manifest_path.read_bytes())

    nested_audit = manifest_source_audit.audit_training_manifest

    def replacing_audit(path: Path) -> dict:
        receipt = nested_audit(path)
        path.rename(original)
        replacement.rename(path)
        return receipt

    monkeypatch.setattr(
        manifest_source_audit,
        "audit_training_manifest",
        replacing_audit,
    )

    with pytest.raises(
        ManifestSourceAuditError,
        match="manifest path identity changed during source audit",
    ):
        audit_manifest_sources(manifest_path)


def test_source_audit_rejects_byte_identical_leaderboard_handoff_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        manifest_path,
        _,
        leaderboard_path,
        _,
        _,
    ) = _fixture(tmp_path)
    replacement = tmp_path / "replacement-leaderboard.json"
    original = tmp_path / "original-leaderboard.json"
    replacement.write_bytes(leaderboard_path.read_bytes())

    nested_audit = manifest_source_audit.audit_leaderboard

    def replacing_audit(path: Path) -> dict:
        receipt = nested_audit(path)
        path.rename(original)
        replacement.rename(path)
        return receipt

    monkeypatch.setattr(
        manifest_source_audit,
        "audit_leaderboard",
        replacing_audit,
    )

    with pytest.raises(
        ManifestSourceAuditError,
        match=(
            "linked leaderboard path identity changed "
            "during source audit"
        ),
    ):
        audit_manifest_sources(manifest_path)

def test_source_audit_reads_manifest_from_held_identity_during_transient_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _, _, _, _ = _fixture(tmp_path)
    original_path = tmp_path / "anchored-manifest.json"
    replacement = tmp_path / "transient-manifest.json"
    replacement.write_bytes(b"not-the-audited-manifest")

    read_bounded = manifest_source_audit._read_fd_bounded
    swapped = False

    def swapping_read(
        fd: int,
        *,
        limit: int,
        label: str,
    ) -> bytes:
        nonlocal swapped
        if label == "manifest" and not swapped:
            manifest_path.rename(original_path)
            replacement.rename(manifest_path)
            try:
                payload = read_bounded(
                    fd,
                    limit=limit,
                    label=label,
                )
            finally:
                manifest_path.rename(replacement)
                original_path.rename(manifest_path)
            swapped = True
            return payload
        return read_bounded(
            fd,
            limit=limit,
            label=label,
        )

    monkeypatch.setattr(
        manifest_source_audit,
        "_read_fd_bounded",
        swapping_read,
    )

    receipt = audit_manifest_sources(manifest_path)

    assert swapped is True
    assert receipt["ok"] is True
    assert receipt["manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()

