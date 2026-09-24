from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class SkillDimensionEstimate:
    mean: float
    uncertainty: float
    effective_weight: float


@dataclass(frozen=True)
class PlayerSkillEstimate:
    player_id: str
    dimensions: dict[str, SkillDimensionEstimate]
    observation_count: int
    effective_weight: float
