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
