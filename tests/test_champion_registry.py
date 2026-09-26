from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.evaluation.champion_registry import (
    promote_champion,
    record_champion_validation,
)


def _write_model(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.npz").write_bytes(b"model-bytes")
    (model_dir / "runtime-model.json").write_text(
        json.dumps({"schema": "runtime"}),
        encoding="utf-8",
    )
    (model_dir / "metrics.json").write_text(
        json.dumps({"schema": "metrics"}),
        encoding="utf-8",
    )


def test_promotes_versioned_bundle_and_current_pointer(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)

    evidence = tmp_path / "promotion.json"
    evidence.write_text(
        json.dumps(
            {
                "promote_future_challenger": True,
                "selected": {
                    "future_assist_config": {
                        "minimum_confidence": 0.6,
                        "minimum_ball_distance": 80,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    registry = tmp_path / "registry"
    result = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence,
        registry_root=registry,
        candidate_name="future-v01",
        code_commit="abc123",
    )

    expected_sha = hashlib.sha256(b"model-bytes").hexdigest()
    assert result["promoted"] is True
    assert result["model_sha256"] == expected_sha
    assert result["behavior_sha256"]
    assert result["version_id"] == (
        f"future-v01-{result['behavior_sha256'][:12]}"
    )

    version_dir = registry / "versions" / result["version_id"]
    assert (version_dir / "model.npz").read_bytes() == b"model-bytes"
    assert (version_dir / "runtime-model.json").is_file()
    assert (version_dir / "metrics.json").is_file()
    assert (version_dir / "promotion-evidence.json").is_file()

    manifest = json.loads((version_dir / "manifest.json").read_text())
    assert manifest["code_commit"] == "abc123"
    assert manifest["runtime_config"]["minimum_confidence"] == 0.6

    current = json.loads((registry / "current.json").read_text())
    assert current["version_id"] == result["version_id"]
    assert current["model_sha256"] == expected_sha
    assert current["runtime_config"]["minimum_ball_distance"] == 80


def test_rejects_failed_promotion_gate(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)

    evidence = tmp_path / "promotion.json"
    evidence.write_text(
        json.dumps({"promote_future_challenger": False}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="explicitly rejects"):
        promote_champion(
            model_dir=model_dir,
            promotion_evidence_path=evidence,
            registry_root=tmp_path / "registry",
            candidate_name="future-v01",
        )

    assert not (tmp_path / "registry" / "current.json").exists()


def test_same_model_promotion_is_idempotent(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)

    evidence = tmp_path / "promotion.json"
    evidence.write_text(
        json.dumps({"promote": True}),
        encoding="utf-8",
    )

    first = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence,
        registry_root=tmp_path / "registry",
        candidate_name="candidate",
    )
    second = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence,
        registry_root=tmp_path / "registry",
        candidate_name="candidate",
    )

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["version_id"] == second["version_id"]

    current = json.loads((tmp_path / "registry" / "current.json").read_text())
    version_dir = tmp_path / "registry" / "versions" / first["version_id"]
    assert current["model_path"] == str(version_dir / "model.npz")
    assert current["runtime_model_path"] == str(version_dir / "runtime-model.json")
    assert current["metrics_path"] == str(version_dir / "metrics.json")
    assert Path(current["model_path"]).is_file()
    assert Path(current["runtime_model_path"]).is_file()
    assert Path(current["metrics_path"]).is_file()


def test_runtime_config_changes_behavior_version(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)
    registry = tmp_path / "registry"

    evidence_a = tmp_path / "promotion-a.json"
    evidence_a.write_text(
        json.dumps(
            {
                "promote_future_challenger": True,
                "selected": {
                    "future_assist_config": {
                        "minimum_confidence": 0.45,
                        "minimum_ball_distance": 80,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    evidence_b = tmp_path / "promotion-b.json"
    evidence_b.write_text(
        json.dumps(
            {
                "promote_future_challenger": True,
                "selected": {
                    "future_assist_config": {
                        "minimum_confidence": 0.75,
                        "minimum_ball_distance": 80,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    first = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence_a,
        registry_root=registry,
        candidate_name="future-v01",
    )
    second = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence_b,
        registry_root=registry,
        candidate_name="future-v01",
    )

    assert first["model_sha256"] == second["model_sha256"]
    assert first["behavior_sha256"] != second["behavior_sha256"]
    assert first["version_id"] != second["version_id"]

    current = json.loads((registry / "current.json").read_text())
    assert current["version_id"] == second["version_id"]
    assert current["runtime_config"]["minimum_confidence"] == 0.75



def _promoted_registry(tmp_path: Path) -> tuple[Path, dict]:
    model_dir = tmp_path / "model-validation"
    _write_model(model_dir)
    evidence = tmp_path / "promotion-validation.json"
    evidence.write_text(json.dumps({"promote": True}), encoding="utf-8")
    registry = tmp_path / "registry-validation"
    result = promote_champion(
        model_dir=model_dir,
        promotion_evidence_path=evidence,
        registry_root=registry,
        candidate_name="candidate",
    )
    return registry, result


def test_records_multi_replay_validation_without_mutating_model_bundle(
    tmp_path: Path,
) -> None:
    registry, promoted = _promoted_registry(tmp_path)
    manifest_path = (
        registry / "versions" / promoted["version_id"] / "manifest.json"
    )
    manifest_before = manifest_path.read_bytes()

    evidence = tmp_path / "multi-replay.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "haxlab-multi-replay-champion-validation-v1",
                "validated": True,
                "candidate": {"version_id": promoted["version_id"]},
                "aggregate": {
                    "replay_count": 5,
                    "mean_movement_delta": 0.064,
                    "mean_progression_delta": -0.014,
                    "total_runtime_errors": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    result = record_champion_validation(
        registry_root=registry,
        validation_evidence_path=evidence,
        stage="multi_replay",
    )

    assert result["recorded"] is True
    assert result["version_id"] == promoted["version_id"]
    assert result["stage"] == "multi_replay"
    assert Path(result["evidence_path"]).is_file()
    assert manifest_path.read_bytes() == manifest_before

    current = json.loads((registry / "current.json").read_text())
    assert current["validation_stage"] == "multi_replay"
    assert current["validation_summary"]["replay_count"] == 5
    assert current["validation_summary"]["total_runtime_errors"] == 0
    assert Path(current["validation_evidence_path"]).is_file()


def test_validation_rejects_failed_gate(tmp_path: Path) -> None:
    registry, promoted = _promoted_registry(tmp_path)
    evidence = tmp_path / "failed-validation.json"
    evidence.write_text(
        json.dumps(
            {
                "validated": False,
                "candidate": {"version_id": promoted["version_id"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not pass"):
        record_champion_validation(
            registry_root=registry,
            validation_evidence_path=evidence,
            stage="multi_replay",
        )

    current = json.loads((registry / "current.json").read_text())
    assert "validation_stage" not in current


def test_validation_rejects_wrong_champion_version(tmp_path: Path) -> None:
    registry, _ = _promoted_registry(tmp_path)
    evidence = tmp_path / "wrong-version.json"
    evidence.write_text(
        json.dumps(
            {
                "validated": True,
                "candidate": {"version_id": "other-version"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version mismatch"):
        record_champion_validation(
            registry_root=registry,
            validation_evidence_path=evidence,
            stage="multi_replay",
        )


def test_validation_stage_cannot_downgrade(tmp_path: Path) -> None:
    registry, promoted = _promoted_registry(tmp_path)
    evidence = tmp_path / "canary.json"
    evidence.write_text(
        json.dumps(
            {
                "validated": True,
                "candidate": {"version_id": promoted["version_id"]},
            }
        ),
        encoding="utf-8",
    )

    record_champion_validation(
        registry_root=registry,
        validation_evidence_path=evidence,
        stage="canary",
    )

    with pytest.raises(ValueError, match="stage downgrade"):
        record_champion_validation(
            registry_root=registry,
            validation_evidence_path=evidence,
            stage="multi_replay",
        )
