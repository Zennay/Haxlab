from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType


def _require_canonical_nonempty_string(
    value: object,
    error: str,
) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(error)


def _require_finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(field_name)
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        raise ValueError(field_name)
    if maximum is not None and numeric > maximum:
        raise ValueError(field_name)


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

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None:
                continue
            _require_finite_number(
                value,
                f"invalid_performance_vector:{field.name}",
            )


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

    def __post_init__(self) -> None:
        _require_canonical_nonempty_string(
            self.player_id,
            "invalid_skill_observation:player_id",
        )
        if type(self.performance) is not PerformanceVector:
            raise ValueError("invalid_skill_observation:performance")

        _require_finite_number(
            self.match_quality_weight,
            "invalid_skill_observation:match_quality_weight",
            minimum=0.0,
            maximum=1.0,
        )
        _require_finite_number(
            self.minutes_or_possessions_weight,
            "invalid_skill_observation:minutes_or_possessions_weight",
            minimum=0.0,
        )

        for field_name in (
            "teammate_context",
            "opponent_context",
            "state_difficulty",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_finite_number(
                    value,
                    f"invalid_skill_observation:{field_name}",
                )

        if self.role is not None:
            _require_canonical_nonempty_string(
                self.role,
                "invalid_skill_observation:role",
            )


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
        _require_canonical_nonempty_string(
            self.player_id,
            "invalid_player_skill_estimate:player_id",
        )
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
