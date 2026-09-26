from copy import deepcopy

import pytest

from haxlab.evaluation.closed_loop_arena import (
    compare_arena_artifacts,
    validate_arena_artifact,
)


def _summary(score: float, guard: float) -> dict:
    return {
        "episodes": 12,
        "wins": 4,
        "draws": 6,
        "losses": 2,
        "match_score": score,
        "goal_differential": 1,
        "territory": {
            "elite_half_rate": 0.55,
            "elite_attack_third_rate": 0.31,
        },
        "progression_share": 0.52,
        "team_shape": {
            "formation_order_rate": 0.8,
            "mean_x_span": 400,
            "mean_std_x_span": 25,
            "mean_pairwise_distance": 220,
            "mean_std_pairwise_distance": 18,
        },
        "runtime_errors": 0,
        "guard_overrides": 10,
        "guard_intervention_rate": guard,
        "roles": {},
    }


def _artifact(model_sha: str, score: float = 0.55) -> dict:
    raw = _summary(score, 0.0)
    guarded = _summary(score + 0.05, 0.02)
    source = {
        "source_id": "source-01",
        "replay_sha256": "a" * 64,
        "source_json_sha256": "b" * 64,
        "stadium_sha256": "c" * 64,
        "scenario_sha256": "d" * 64,
        "scenario_count": 16,
        "raw": raw,
        "guarded": guarded,
        "guard_dependency": {
            "guard_intervention_rate": 0.02,
            "match_score_delta": 0.05,
            "progression_delta": 0.0,
            "territory_delta": 0.0,
            "formation_order_delta": 0.0,
        },
    }
    return {
        "schema": "haxlab-closed-loop-arena-v2",
        "provenance": {
            "code_ref": "arena-code-ref",
            "model_sha256": model_sha,
            "suite_manifest_sha256": "e" * 64,
            "config_sha256": "f" * 64,
            "source_replay_sha256s": [
                "a" * 64,
                "1" * 64,
                "2" * 64,
            ],
            "stadium_sha256s": ["c" * 64] * 3,
            "scenario_sha256s": [
                "d" * 64,
                "3" * 64,
                "4" * 64,
            ],
        },
        "aggregate": {
            "raw": raw,
            "guarded": guarded,
            "guard_dependency": source["guard_dependency"],
        },
        "sources": [
            source,
            {
                **deepcopy(source),
                "source_id": "source-02",
                "replay_sha256": "1" * 64,
                "scenario_sha256": "3" * 64,
            },
            {
                **deepcopy(source),
                "source_id": "source-03",
                "replay_sha256": "2" * 64,
                "scenario_sha256": "4" * 64,
            },
        ],
    }


def test_arena_comparison_requires_identical_provenance() -> None:
    baseline = _artifact("0" * 64)
    challenger = _artifact("9" * 64, 0.60)

    result = compare_arena_artifacts(baseline, challenger)

    assert result["aggregate"]["raw"]["match_score"]["delta"] == pytest.approx(
        0.05
    )
    assert len(result["sources"]) == 3


def test_arena_comparison_rejects_suite_mismatch() -> None:
    baseline = _artifact("0" * 64)
    challenger = _artifact("9" * 64)
    challenger["provenance"]["suite_manifest_sha256"] = "8" * 64

    with pytest.raises(ValueError, match="suite_manifest_sha256"):
        compare_arena_artifacts(baseline, challenger)


def test_arena_comparison_rejects_same_model() -> None:
    baseline = _artifact("0" * 64)
    challenger = _artifact("0" * 64)

    with pytest.raises(ValueError, match="distinct model"):
        compare_arena_artifacts(baseline, challenger)


def test_arena_validation_requires_complete_rollout_coverage() -> None:
    artifact = _artifact("0" * 64)
    artifact["aggregate"]["guarded"]["episodes"] = 11

    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_arena_artifact(artifact)
