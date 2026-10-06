from __future__ import annotations

import math
from dataclasses import fields

from haxlab.skill.models import (
    PerformanceVector,
    PlayerSkillEstimate,
    SkillDimensionEstimate,
    SkillObservation,
)


def _require_finite_number(value: object, field_name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"invalid_numeric_evidence:{field_name}")
    return float(value)


def _validate_observation(observation: SkillObservation) -> None:
    _require_finite_number(
        observation.match_quality_weight,
        "match_quality_weight",
    )
    _require_finite_number(
        observation.minutes_or_possessions_weight,
        "minutes_or_possessions_weight",
    )

    for field_name, value in (
        ("teammate_context", observation.teammate_context),
        ("opponent_context", observation.opponent_context),
        ("state_difficulty", observation.state_difficulty),
    ):
        if value is not None:
            _require_finite_number(value, field_name)

    for field in fields(PerformanceVector):
        value = getattr(observation.performance, field.name)
        if value is not None:
            _require_finite_number(value, f"performance.{field.name}")


def _context_adjustment(observation: SkillObservation) -> float:
    """Small V0 contextual correction, intentionally bounded.

    Opponent strength is *not* the skill estimate. It only modifies how surprising
    an observed performance was. Teammate context pushes the other way.
    """
    opponent = (
        0.0
        if observation.opponent_context is None
        else float(observation.opponent_context)
    )
    teammate = (
        0.0
        if observation.teammate_context is None
        else float(observation.teammate_context)
    )
    difficulty = (
        0.0
        if observation.state_difficulty is None
        else float(observation.state_difficulty)
    )

    adjustment = 0.10 * opponent - 0.10 * teammate + 0.10 * difficulty
    return max(-0.25, min(0.25, adjustment))


def estimate_player_skill_v0(
    player_id: str,
    observations: list[SkillObservation],
    *,
    prior_mean: float = 0.0,
    prior_weight: float = 5.0,
) -> PlayerSkillEstimate:
    """Interpretable shrinkage estimator for early HaxLab development.

    This is deliberately not Elo. It aggregates normalized individual performance
    dimensions and applies only a bounded context correction.
    """
    prior_mean_value = _require_finite_number(prior_mean, "prior_mean")
    prior_weight_value = _require_finite_number(prior_weight, "prior_weight")
    if prior_weight_value <= 0:
        raise ValueError("prior_weight_must_be_positive")

    own = [item for item in observations if item.player_id == player_id]
    for observation in own:
        _validate_observation(observation)

    estimates: dict[str, SkillDimensionEstimate] = {}

    for field in fields(PerformanceVector):
        weighted_sum = prior_mean_value * prior_weight_value
        total_weight = prior_weight_value

        for observation in own:
            value = getattr(observation.performance, field.name)
            if value is None:
                continue

            quality = max(0.0, min(1.0, float(observation.match_quality_weight)))
            exposure = max(0.0, float(observation.minutes_or_possessions_weight))
            weight = quality * exposure
            if weight <= 0:
                continue

            adjusted = float(value) + _context_adjustment(observation)
            weighted_sum += adjusted * weight
            total_weight += weight

        mean = weighted_sum / total_weight
        # Simple transparent uncertainty proxy for V0. This will be replaced by a
        # hierarchical/probabilistic model once enough data exists.
        uncertainty = 1.0 / (total_weight ** 0.5)

        estimates[field.name] = SkillDimensionEstimate(
            mean=round(mean, 6),
            uncertainty=round(uncertainty, 6),
            effective_weight=round(total_weight - prior_weight_value, 6),
        )

    effective_weight = sum(
        max(0.0, min(1.0, float(item.match_quality_weight)))
        * max(0.0, float(item.minutes_or_possessions_weight))
        for item in own
    )

    return PlayerSkillEstimate(
        player_id=player_id,
        dimensions=estimates,
        observation_count=len(own),
        effective_weight=round(effective_weight, 6),
    )
