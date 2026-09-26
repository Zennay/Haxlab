from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.evaluation.champion_registry import promote_champion


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
    assert result["version_id"] == f"future-v01-{expected_sha[:12]}"

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
