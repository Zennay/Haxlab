from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType


@dataclass(frozen=True)
class PerformanceVector:
    """Role-aware normalized player evidence for one observation window.

    Values are expected to be comparable residuals or normalized metrics, not raw
    totals. A missing metric is represented by None and must not be treated as zero.
    """

    retention: float | None = None
    progression: float | None = None
    creation: float | None = None
    finishing: float | None = None
    defending: float | None = None
    positioning: float | None = None
    pressure_recovery: float | None = None
    risk_management: float | None = None


@dataclass(frozen=True)
class SkillObservation:
    player_id: str
    performance: PerformanceVector
    match_quality_weight: float
    minutes_or_possessions_weight: float
    teammate_context: float | None = None
    opponent_context: float | None = None
    state_difficulty: float | None = None
    role: str | None = None


def _require_finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
) -> None:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(field_name)
    if minimum is not None and float(value) < minimum:
        raise ValueError(field_name)


@dataclass(frozen=True)
class SkillDimensionEstimate:
    mean: float
    uncertainty: float
    effective_weight: float

    def __post_init__(self) -> None:
        _require_finite_number(
            self.mean,
            "invalid_skill_dimension_estimate:mean",
        )
        _require_finite_number(
            self.uncertainty,
            "invalid_skill_dimension_estimate:uncertainty",
            minimum=0.0,
        )
        _require_finite_number(
            self.effective_weight,
            "invalid_skill_dimension_estimate:effective_weight",
            minimum=0.0,
        )


@dataclass(frozen=True)
class PlayerSkillEstimate:
    player_id: str
    dimensions: Mapping[str, SkillDimensionEstimate]
    observation_count: int
    effective_weight: float

    def __post_init__(self) -> None:
        if type(self.player_id) is not str or not self.player_id:
            raise ValueError("invalid_player_skill_estimate:player_id")
        if type(self.dimensions) is not dict:
            raise ValueError("invalid_player_skill_estimate:dimensions")

        expected_dimensions = {field.name for field in fields(PerformanceVector)}
        if set(self.dimensions) != expected_dimensions:
            raise ValueError("invalid_player_skill_estimate:dimension_keys")
        if any(
            type(name) is not str
            or type(estimate) is not SkillDimensionEstimate
            for name, estimate in self.dimensions.items()
        ):
            raise ValueError("invalid_player_skill_estimate:dimension_value")

        object.__setattr__(
            self,
            "dimensions",
            MappingProxyType(dict(self.dimensions)),
        )

        if type(self.observation_count) is not int or self.observation_count < 0:
            raise ValueError("invalid_player_skill_estimate:observation_count")
        _require_finite_number(
            self.effective_weight,
            "invalid_player_skill_estimate:effective_weight",
            minimum=0.0,
        )
