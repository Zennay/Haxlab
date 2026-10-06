import json
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
    preregistration_issues,
    metric_snapshot,
    sha256_file,
)


@pytest.mark.parametrize("value", [True, 1.5, "2", 0, -1])
def test_generation_loop_rejects_invalid_tick_budget(
    tmp_path,
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_generations_per_tick must be a native positive integer",
    ):
        GenerationLoop(
            app_dir=tmp_path,
            state_dir=tmp_path / "state",
            models_dir=tmp_path / "models",
            derived_dir=tmp_path / "derived",
            analysis_version="analysis-v4",
            manifest=tmp_path / "manifest.json",
            shard_root=tmp_path / "shards",
            max_generations_per_tick=value,  # type: ignore[arg-type]
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
            "generation": 1,
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


@pytest.mark.parametrize("value", [True, 2.0, "2", 0, -1])
def test_existing_generation_state_rejects_invalid_next_generation(
    tmp_path,
    value: object,
) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["next_generation"] = value
    loop.save_state(state)

    with pytest.raises(
        RuntimeError,
        match="generation_state.next_generation_invalid",
    ):
        loop.initialize()


def test_existing_generation_state_rejects_generation_rewind(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["next_generation"] = 1
    loop.save_state(state)

    with pytest.raises(
        RuntimeError,
        match="generation_state.next_generation_not_after_champion",
    ):
        loop.initialize()


def test_existing_generation_state_rejects_champion_id_generation_drift(
    tmp_path,
) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["champion"]["id"] = "gen-0007"
    loop.save_state(state)

    with pytest.raises(
        RuntimeError,
        match="generation_state.champion_id_generation_mismatch",
    ):
        loop.initialize()


def test_existing_generation_state_rejects_active_generation_drift(
    tmp_path,
) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["active"] = {"generation": 3}
    loop.save_state(state)

    with pytest.raises(
        RuntimeError,
        match="generation_state.active_generation_mismatch",
    ):
        loop.initialize()


def test_existing_generation_state_accepts_matching_active_generation(
    tmp_path,
) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    state["active"] = {"generation": 2}
    loop.save_state(state)

    loaded = loop.initialize()

    assert loaded["active"]["generation"] == 2



def test_cached_preregistration_reuses_exact_evaluation_provenance(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    preregistration = loop.preregister(state, 2)

    cached = loop.preregister(state, 2)

    assert cached == preregistration
    assert preregistration_issues(
        cached,
        state=state,
        generation=2,
        analysis_version=loop.analysis_version,
        manifest_sha256=sha256_file(loop.manifest),
        shard_root=loop.shard_root,
    ) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("parent_champion", "wrong-champion", "parent_champion_mismatch"),
        ("analysis_version", "analysis-v3", "analysis_version_mismatch"),
        ("dataset_manifest_sha256", "0" * 64, "manifest_sha256_mismatch"),
    ],
)
def test_cached_preregistration_rejects_provenance_drift(
    tmp_path,
    field: str,
    value: object,
    reason: str,
) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    preregistration = loop.preregister(state, 2)
    preregistration[field] = value
    prereg_path = loop.generations_dir / "gen-0002" / "preregistration.json"
    prereg_path.write_text(
        json.dumps(preregistration, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=reason):
        loop.preregister(state, 2)


def test_cached_preregistration_rejects_hyperparameter_mutation(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    preregistration = loop.preregister(state, 2)
    preregistration["hyperparameters"]["learning_rate"] = 0.99
    prereg_path = loop.generations_dir / "gen-0002" / "preregistration.json"
    prereg_path.write_text(
        json.dumps(preregistration, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="preregistration.hyperparameters_mismatch"):
        loop.preregister(state, 2)


def test_cached_preregistration_rejects_unfrozen_holdout(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    preregistration = loop.preregister(state, 2)
    preregistration["split"]["holdout_is_frozen"] = False
    prereg_path = loop.generations_dir / "gen-0002" / "preregistration.json"
    prereg_path.write_text(
        json.dumps(preregistration, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="preregistration.holdout_not_frozen"):
        loop.preregister(state, 2)


def test_cached_preregistration_rejects_hash_tampering(tmp_path) -> None:
    loop, state = _existing_generation_loop(tmp_path)
    preregistration = loop.preregister(state, 2)
    preregistration["preregistration_sha256"] = "f" * 64
    prereg_path = loop.generations_dir / "gen-0002" / "preregistration.json"
    prereg_path.write_text(
        json.dumps(preregistration, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="preregistration.sha256_mismatch"):
        loop.preregister(state, 2)
