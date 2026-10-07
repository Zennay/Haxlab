from __future__ import annotations

import math
from dataclasses import fields

import pytest

from haxlab.skill.estimator import estimate_player_skill_v0
from haxlab.skill.models import (
    PerformanceVector,
    PlayerSkillEstimate,
    SkillDimensionEstimate,
)


def _dimensions() -> dict[str, SkillDimensionEstimate]:
    return {
        field.name: SkillDimensionEstimate(
            mean=0.0,
            uncertainty=1.0,
            effective_weight=0.0,
        )
        for field in fields(PerformanceVector)
    }


def test_current_estimator_output_satisfies_model_contract() -> None:
    estimate = estimate_player_skill_v0("player-a", [])

    assert estimate.player_id == "player-a"
    assert set(estimate.dimensions) == {
        field.name for field in fields(PerformanceVector)
    }


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("mean", math.nan, "mean"),
        ("mean", math.inf, "mean"),
        ("mean", True, "mean"),
        ("uncertainty", -0.01, "uncertainty"),
        ("uncertainty", math.nan, "uncertainty"),
        ("effective_weight", -0.01, "effective_weight"),
        ("effective_weight", math.inf, "effective_weight"),
    ],
)
def test_skill_dimension_estimate_rejects_malformed_values(
    field_name: str,
    value: object,
    reason: str,
) -> None:
    kwargs = {
        "mean": 0.0,
        "uncertainty": 1.0,
        "effective_weight": 0.0,
    }
    kwargs[field_name] = value

    with pytest.raises(
        ValueError,
        match=f"invalid_skill_dimension_estimate:{reason}",
    ):
        SkillDimensionEstimate(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("player_id", ["", None, 123])
def test_player_skill_estimate_requires_native_nonempty_player_id(
    player_id: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:player_id",
    ):
        PlayerSkillEstimate(
            player_id=player_id,  # type: ignore[arg-type]
            dimensions=_dimensions(),
            observation_count=0,
            effective_weight=0.0,
        )


def test_player_skill_estimate_requires_exact_dimension_surface() -> None:
    missing = _dimensions()
    missing.pop("retention")

    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:dimension_keys",
    ):
        PlayerSkillEstimate(
            player_id="player-a",
            dimensions=missing,
            observation_count=0,
            effective_weight=0.0,
        )

    extra = _dimensions()
    extra["future_metric"] = SkillDimensionEstimate(0.0, 1.0, 0.0)

    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:dimension_keys",
    ):
        PlayerSkillEstimate(
            player_id="player-a",
            dimensions=extra,
            observation_count=0,
            effective_weight=0.0,
        )


def test_player_skill_estimate_requires_dimension_estimate_values() -> None:
    dimensions = _dimensions()
    dimensions["retention"] = object()  # type: ignore[assignment]

    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:dimension_value",
    ):
        PlayerSkillEstimate(
            player_id="player-a",
            dimensions=dimensions,
            observation_count=0,
            effective_weight=0.0,
        )


@pytest.mark.parametrize("observation_count", [-1, True, 1.0])
def test_player_skill_estimate_rejects_invalid_observation_count(
    observation_count: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:observation_count",
    ):
        PlayerSkillEstimate(
            player_id="player-a",
            dimensions=_dimensions(),
            observation_count=observation_count,  # type: ignore[arg-type]
            effective_weight=0.0,
        )


@pytest.mark.parametrize(
    "effective_weight",
    [-0.01, math.nan, math.inf, True, "1.0"],
)
def test_player_skill_estimate_rejects_invalid_effective_weight(
    effective_weight: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_player_skill_estimate:effective_weight",
    ):
        PlayerSkillEstimate(
            player_id="player-a",
            dimensions=_dimensions(),
            observation_count=0,
            effective_weight=effective_weight,  # type: ignore[arg-type]
        )


def test_player_skill_estimate_dimensions_are_defensively_immutable() -> None:
    source = _dimensions()
    estimate = PlayerSkillEstimate(
        player_id="player-a",
        dimensions=source,
        observation_count=0,
        effective_weight=0.0,
    )

    source.pop("retention")
    assert "retention" in estimate.dimensions

    with pytest.raises(TypeError):
        estimate.dimensions["retention"] = SkillDimensionEstimate(
            mean=1.0,
            uncertainty=0.0,
            effective_weight=1.0,
        )  # type: ignore[index]


def test_estimator_dimensions_remain_mapping_compatible() -> None:
    estimate = estimate_player_skill_v0("player-a", [])

    assert list(estimate.dimensions) == [
        field.name for field in fields(PerformanceVector)
    ]
    assert estimate.dimensions["retention"].mean == 0.0


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("retention", math.nan),
        ("progression", math.inf),
        ("creation", -math.inf),
        ("finishing", True),
        ("defending", "1.0"),
    ],
)
def test_performance_vector_rejects_non_finite_or_non_native_values(
    field_name: str,
    value: object,
) -> None:
    kwargs = {field_name: value}

    with pytest.raises(
        ValueError,
        match=f"invalid_performance_vector:{field_name}",
    ):
        PerformanceVector(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"player_id": ""}, "player_id"),
        ({"player_id": "   "}, "player_id"),
        ({"player_id": 123}, "player_id"),
        ({"performance": object()}, "performance"),
        ({"match_quality_weight": -0.01}, "match_quality_weight"),
        ({"match_quality_weight": 1.01}, "match_quality_weight"),
        ({"match_quality_weight": math.nan}, "match_quality_weight"),
        ({"match_quality_weight": True}, "match_quality_weight"),
        (
            {"minutes_or_possessions_weight": -0.01},
            "minutes_or_possessions_weight",
        ),
        (
            {"minutes_or_possessions_weight": math.inf},
            "minutes_or_possessions_weight",
        ),
        (
            {"minutes_or_possessions_weight": False},
            "minutes_or_possessions_weight",
        ),
        ({"teammate_context": math.nan}, "teammate_context"),
        ({"opponent_context": math.inf}, "opponent_context"),
        ({"state_difficulty": True}, "state_difficulty"),
        ({"role": ""}, "role"),
        ({"role": "   "}, "role"),
        ({"role": 1}, "role"),
    ],
)
def test_skill_observation_rejects_malformed_evidence(
    overrides: dict[str, object],
    reason: str,
) -> None:
    values: dict[str, object] = {
        "player_id": "player-a",
        "performance": PerformanceVector(retention=0.5),
        "match_quality_weight": 1.0,
        "minutes_or_possessions_weight": 10.0,
        "teammate_context": 0.0,
        "opponent_context": 0.0,
        "state_difficulty": 0.0,
        "role": "defender",
    }
    values.update(overrides)

    with pytest.raises(
        ValueError,
        match=f"invalid_skill_observation:{reason}",
    ):
        SkillObservation(**values)  # type: ignore[arg-type]


def test_skill_observation_accepts_current_leaderboard_boundary_values() -> None:
    observation = SkillObservation(
        player_id="auth:abc123",
        performance=PerformanceVector(
            retention=-3.0,
            progression=0.0,
            creation=3.0,
            finishing=None,
            defending=1.25,
            positioning=-0.5,
            pressure_recovery=0.75,
            risk_management=-1.0,
        ),
        match_quality_weight=0.15,
        minutes_or_possessions_weight=2.0,
        teammate_context=-1.0,
        opponent_context=1.0,
        state_difficulty=None,
        role="midfield",
    )

    assert observation.performance.creation == 3.0
    assert observation.match_quality_weight == 0.15
    assert observation.minutes_or_possessions_weight == 2.0
