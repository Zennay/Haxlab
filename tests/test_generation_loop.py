import math

import pytest

from haxlab.research.generation_loop import (
    GenerationLoop,
    SCHEMA,
    composite_score,
    evaluate_candidate,
    experiment_for_generation,
    generation_state_issues,
    mine_failure,
    metric_snapshot,
    sha256_file,
)


def test_generation_experiments_are_deterministic_and_bounded() -> None:
    first = experiment_for_generation(1)
    again = experiment_for_generation(1)
    later = experiment_for_generation(9)

    assert first == again
    assert first["seed"] != later["seed"]
    assert 8 <= first["hidden_dim"] <= 128
    assert 1 <= first["epochs"] <= 8
    assert first["learning_rate"] > 0
    assert first["l2"] >= 0


def test_rejected_generation_still_has_failure_inventory() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    candidate = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    decision = evaluate_candidate(candidate, champion)
    failure = mine_failure(7, candidate, champion, decision)

    assert decision["promote"] is False
    assert "insufficient_improvement" in " ".join(decision["reasons"])
    assert failure["next_action"] == "continue_with_next_bounded_experiment"
    assert failure["generation"] == 7


def test_promotion_requires_real_holdout_improvement() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000,
    }

    decision = evaluate_candidate(candidate, champion)

    assert composite_score(candidate) > composite_score(champion)
    assert decision["promote"] is True
    assert decision["improvement"] >= 0.001


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("joint_accuracy", "0.90", "non_numeric"),
        ("joint_accuracy", math.inf, "non_finite"),
        ("kick_f1", 1.01, "above_maximum"),
        ("direction_accuracy", -0.01, "below_minimum"),
        ("samples", 12.5, "not_integer"),
        ("samples", True, "non_numeric"),
    ],
)
def test_metric_snapshot_rejects_malformed_holdout_evidence(
    field: str,
    value: object,
    reason: str,
) -> None:
    holdout = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    holdout[field] = value
    with pytest.raises(ValueError, match=rf"invalid_final_holdout\.{field}:{reason}"):
        metric_snapshot({"final_holdout": holdout})


def test_evaluate_candidate_fails_closed_on_non_finite_candidate() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000.0,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": math.inf,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }

    decision = evaluate_candidate(candidate, champion)

    assert decision["promote"] is False
    assert decision["candidate_score"] is None
    assert "invalid_candidate.joint_accuracy:non_finite" in decision["reasons"]


def test_evaluate_candidate_fails_closed_on_corrupt_champion_snapshot() -> None:
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": "0.40",
        "samples": 1000.0,
    }

    decision = evaluate_candidate(candidate, champion)  # type: ignore[arg-type]

    assert decision["promote"] is False
    assert "invalid_champion.kick_f1:non_numeric" in decision["reasons"]


def test_evaluate_candidate_rejects_malformed_policy_threshold() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000.0,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }

    decision = evaluate_candidate(
        candidate,
        champion,
        maximum_kick_regression=2.0,
    )

    assert decision["promote"] is False
    assert "invalid_policy.maximum_kick_regression:above_maximum" in decision["reasons"]



def _existing_generation_loop(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema":"test-manifest-v1"}\n', encoding="utf-8")
    state_dir = tmp_path / "state"
    models_dir = tmp_path / "models"
    shard_root = tmp_path / "shards"
    shard_root.mkdir()
    loop = GenerationLoop(
        app_dir=tmp_path,
        state_dir=state_dir,
        models_dir=models_dir,
        derived_dir=tmp_path / "derived",
        analysis_version="analysis-v4",
        manifest=manifest,
        shard_root=shard_root,
    )
    champion_metrics = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000.0,
    }
    state = {
        "schema": SCHEMA,
        "analysis_version": "analysis-v4",
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "shard_root": str(shard_root),
        "next_generation": 2,
        "champion": {
            "id": "gen-0001",
            "metrics": champion_metrics,
            "score": composite_score(champion_metrics),
        },
        "active": None,
    }
    loop.save_state(state)
    return loop, state


def test_existing_generation_state_reuses_matching_provenance(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)

    loaded = loop.initialize()

    assert loaded["champion"]["id"] == state["champion"]["id"]
    assert generation_state_issues(
        loaded,
        analysis_version=loop.analysis_version,
        manifest_sha256=sha256_file(loop.manifest),
    ) == []


def test_existing_generation_state_rejects_changed_manifest(tmp_path) -> None:
    loop, _ = _existing_generation_loop(tmp_path)
    loop.manifest.write_text('{"schema":"different-manifest"}\n', encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="generation_state.manifest_sha256_mismatch",
    ):
        loop.initialize()


def test_existing_generation_state_rejects_analysis_version_change(tmp_path) -> None:
    loop, _ = _existing_generation_loop(tmp_path)
    changed = GenerationLoop(
        app_dir=loop.app_dir,
        state_dir=loop.state_dir,
        models_dir=loop.models_dir,
        derived_dir=loop.derived_dir,
        analysis_version="analysis-v5",
        manifest=loop.manifest,
        shard_root=loop.shard_root,
    )

    with pytest.raises(
        RuntimeError,
        match="generation_state.analysis_version_mismatch",
    ):
        changed.initialize()


def test_existing_generation_state_rejects_tampered_champion_score(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["champion"]["score"] = 0.99
    loop.save_state(state)

    with pytest.raises(
        RuntimeError,
        match="generation_state.champion_score_mismatch",
    ):
        loop.initialize()
