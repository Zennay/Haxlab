from __future__ import annotations

import json
from pathlib import Path

import haxlab.learning.evolution as evolution


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
        "match_results": [
            {
                "policy": {
                    "total_actions": 1200,
                    "total_kicks": 60,
                }
            }
            for _ in range(12)
        ],
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


def test_bootstrap_evolution_cycle_needs_no_duel(tmp_path: Path) -> None:
    metrics = _write_json(tmp_path / "metrics.json", _metrics())
    sandbox = _write_json(tmp_path / "sandbox.json", _sandbox())
    model = _model_dir(tmp_path, "candidate")

    result = evolution.run_evolution_cycle(
        registry_path=tmp_path / "champions" / "registry.json",
        champions_root=tmp_path / "champions",
        candidate_id="candidate-a",
        candidate_model_dir=model,
        metrics_path=metrics,
        sandbox_path=sandbox,
        stadium_path=tmp_path / "unused.hbs",
        duel_script=tmp_path / "unused.js",
        source_ref="sha-a",
    )

    assert result["had_existing_champion"] is False
    assert result["duel_path"] is None
    assert result["promotion"]["promoted"] is True


def test_existing_champion_runs_duel_before_promotion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metrics = _write_json(tmp_path / "metrics.json", _metrics())
    sandbox = _write_json(tmp_path / "sandbox.json", _sandbox())
    stadium = _write_json(tmp_path / "stadium.hbs", {"name": "Test"})
    duel_script = tmp_path / "duel.js"
    duel_script.write_text("// fake", encoding="utf-8")
    registry = tmp_path / "champions" / "registry.json"
    champions_root = registry.parent

    first = evolution.run_evolution_cycle(
        registry_path=registry,
        champions_root=champions_root,
        candidate_id="candidate-a",
        candidate_model_dir=_model_dir(tmp_path, "candidate-a"),
        metrics_path=metrics,
        sandbox_path=sandbox,
        stadium_path=stadium,
        duel_script=duel_script,
    )
    assert first["promotion"]["promoted"] is True

    class Completed:
        returncode = 0
        stdout = "{}"
        stderr = ""

    seen: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        output_index = command.index("--output") + 1
        Path(command[output_index]).write_text(
            json.dumps(_duel()),
            encoding="utf-8",
        )
        return Completed()

    monkeypatch.setattr(evolution.subprocess, "run", fake_run)

    second = evolution.run_evolution_cycle(
        registry_path=registry,
        champions_root=champions_root,
        candidate_id="candidate-b",
        candidate_model_dir=_model_dir(tmp_path, "candidate-b"),
        metrics_path=metrics,
        sandbox_path=sandbox,
        stadium_path=stadium,
        duel_script=duel_script,
        source_ref="sha-b",
    )

    assert second["had_existing_champion"] is True
    assert "--challenger" in seen["command"]
    assert "--champion" in seen["command"]
    assert second["promotion"]["promoted"] is True
    current = json.loads(registry.read_text(encoding="utf-8"))["current"]
    assert current["candidate_id"] == "candidate-b"