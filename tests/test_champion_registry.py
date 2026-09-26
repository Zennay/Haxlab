from __future__ import annotations

import json
from pathlib import Path

from haxlab.evaluation.champion_registry import (
    load_registry,
    promote_candidate,
)


def _metrics() -> dict:
    return {
        "training": {
            "frozen_holdout_used_for_selection": False,
            "kick_threshold_source": "validation_only",
            "kick_calibration": {"constraint_satisfied": True},
        },
        "final_validation": {"direction_accuracy": 0.43},
        "final_holdout": {
            "samples": 100_000,
            "direction_accuracy": 0.42,
            "kick_f1": 0.10,
            "kick_true_rate": 0.04,
            "kick_predicted_rate": 0.05,
            "baselines": {"majority_direction_accuracy": 0.18},
            "by_role": {
                role: {
                    "direction_accuracy": 0.40,
                    "baselines": {"majority_direction_accuracy": 0.18},
                }
                for role in ("gk", "dm", "am", "st")
            },
        },
    }


def _sandbox() -> dict:
    rows = [
        {
            "policy": {
                "total_actions": 1200,
                "total_kicks": 60,
            }
        }
        for _ in range(12)
    ]
    return {
        "schema": "haxlab-elite-sandbox-benchmark-v1",
        "matches": 12,
        "wins": 3,
        "draws": 7,
        "losses": 2,
        "non_loss_rate": 10 / 12,
        "runtime_errors": 0,
        "territory": {
            "elite_half_rate": 0.46,
            "baseline_half_rate": 0.44,
            "elite_attack_third_rate": 0.31,
            "baseline_attack_third_rate": 0.29,
        },
        "by_elite_side": {
            "1": {"elite_half_rate": 0.48},
            "2": {"elite_half_rate": 0.44},
        },
        "by_profile": {
            "balanced": {},
            "compact": {},
            "press": {},
        },
        "match_results": rows,
    }


def _duel() -> dict:
    return {
        "schema": "haxlab-elite-model-duel-v1",
        "matches": 12,
        "wins": 5,
        "draws": 4,
        "losses": 3,
        "territory": {
            "challenger_half_rate": 0.52,
            "champion_half_rate": 0.43,
            "challenger_attack_third_rate": 0.31,
            "champion_attack_third_rate": 0.28,
        },
        "challenger_runtime_errors": 0,
        "champion_runtime_errors": 0,
        "challenger_kick_action_rate": 0.05,
        "by_challenger_side": {
            "1": {"challenger_half_rate": 0.54},
            "2": {"challenger_half_rate": 0.50},
        },
        "goals": {
            "challenger": 7,
            "champion": 5,
            "differential": 2,
        },
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _model_dir(root: Path, name: str) -> Path:
    model = root / name
    model.mkdir()
    (model / "model.npz").write_bytes(b"npz")
    (model / "metrics.json").write_text("{}", encoding="utf-8")
    (model / "runtime-model.json").write_text("{}", encoding="utf-8")
    return model


def test_first_candidate_bootstraps_champion_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "champions" / "registry.json"
    champions_root = registry_path.parent
    model = _model_dir(tmp_path, "candidate-a")
    metrics = _write_json(tmp_path / "metrics-a.json", _metrics())
    sandbox = _write_json(tmp_path / "sandbox-a.json", _sandbox())

    result = promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id="candidate-a",
        candidate_model_dir=model,
        metrics_path=metrics,
        sandbox_path=sandbox,
        source_ref="sha-a",
    )

    assert result["promoted"] is True
    registry = load_registry(registry_path)
    assert registry["current"]["candidate_id"] == "candidate-a"
    assert len(registry["history"]) == 1
    archived = champions_root / "candidate-a"
    assert (archived / "model" / "model.npz").exists()
    assert (archived / "evidence" / "metrics.json").exists()
    assert (champions_root / "current.json").exists()


def test_existing_champion_requires_duel(tmp_path: Path) -> None:
    registry_path = tmp_path / "champions" / "registry.json"
    champions_root = registry_path.parent
    metrics = _write_json(tmp_path / "metrics.json", _metrics())
    sandbox = _write_json(tmp_path / "sandbox.json", _sandbox())

    first = promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id="candidate-a",
        candidate_model_dir=_model_dir(tmp_path, "candidate-a"),
        metrics_path=metrics,
        sandbox_path=sandbox,
    )
    assert first["promoted"]

    second = promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id="candidate-b",
        candidate_model_dir=_model_dir(tmp_path, "candidate-b"),
        metrics_path=metrics,
        sandbox_path=sandbox,
    )
    assert second["promoted"] is False
    assert "duel_required_for_existing_champion" in second["evaluation"]["reasons"]
    assert load_registry(registry_path)["current"]["candidate_id"] == "candidate-a"


def test_duel_winner_replaces_existing_champion(tmp_path: Path) -> None:
    registry_path = tmp_path / "champions" / "registry.json"
    champions_root = registry_path.parent
    metrics = _write_json(tmp_path / "metrics.json", _metrics())
    sandbox = _write_json(tmp_path / "sandbox.json", _sandbox())
    duel = _write_json(tmp_path / "duel.json", _duel())

    promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id="candidate-a",
        candidate_model_dir=_model_dir(tmp_path, "candidate-a"),
        metrics_path=metrics,
        sandbox_path=sandbox,
    )
    result = promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id="candidate-b",
        candidate_model_dir=_model_dir(tmp_path, "candidate-b"),
        metrics_path=metrics,
        sandbox_path=sandbox,
        duel_path=duel,
        source_ref="sha-b",
    )

    assert result["promoted"] is True
    registry = load_registry(registry_path)
    assert registry["current"]["candidate_id"] == "candidate-b"
    assert len(registry["history"]) == 2
    assert registry["history"][-1]["replaced_champion"]["candidate_id"] == "candidate-a"
