from __future__ import annotations

from dataclasses import fields

from haxlab.skill.models import (
    PerformanceVector,
    PlayerSkillEstimate,
    SkillDimensionEstimate,
    SkillObservation,
)


def _context_adjustment(observation: SkillObservation) -> float:
    """Small V0 contextual correction, intentionally bounded.

    Opponent strength is *not* the skill estimate. It only modifies how surprising
    an observed performance was. Teammate context pushes the other way.
    """
    opponent = observation.opponent_context or 0.0
    teammate = observation.teammate_context or 0.0
    difficulty = observation.state_difficulty or 0.0

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
    own = [item for item in observations if item.player_id == player_id]
    estimates: dict[str, SkillDimensionEstimate] = {}

    for field in fields(PerformanceVector):
        weighted_sum = prior_mean * prior_weight
        total_weight = prior_weight
        value_count = 0

        for observation in own:
            value = getattr(observation.performance, field.name)
            if value is None:
                continue

            quality = max(0.0, min(1.0, observation.match_quality_weight))
            exposure = max(0.0, observation.minutes_or_possessions_weight)
            weight = quality * exposure
            if weight <= 0:
                continue

            adjusted = value + _context_adjustment(observation)
            weighted_sum += adjusted * weight
            total_weight += weight
            value_count += 1

        mean = weighted_sum / total_weight
        # Simple transparent uncertainty proxy for V0. This will be replaced by a
        # hierarchical/probabilistic model once enough data exists.
        uncertainty = 1.0 / (total_weight ** 0.5)

        estimates[field.name] = SkillDimensionEstimate(
            mean=round(mean, 6),
            uncertainty=round(uncertainty, 6),
            effective_weight=round(total_weight - prior_weight, 6),
        )

    effective_weight = sum(
        max(0.0, min(1.0, item.match_quality_weight))
        * max(0.0, item.minutes_or_possessions_weight)
        for item in own
    )

    return PlayerSkillEstimate(
        player_id=player_id,
        dimensions=estimates,
        observation_count=len(own),
        effective_weight=round(effective_weight, 6),
    )
