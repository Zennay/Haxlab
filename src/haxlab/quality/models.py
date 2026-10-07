from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class QualityTier(StrEnum):
    REJECTED = "rejected"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    ELITE = "elite"


@dataclass(frozen=True)
class MatchQualityEvidence:
    replay_valid: bool
    duration_seconds: float | None = None
    expected_player_count: int | None = None
    observed_player_count: int | None = None
    disconnect_count: int | None = None
    activity_ratio: float | None = None
    parser_completeness: float | None = None
    stadium_supported: bool | None = None
    has_reliable_player_identities: bool | None = None


def _validate_reason_tuple(value: object, field_name: str) -> None:
    if type(value) is not tuple:
        raise ValueError(f"invalid_quality_assessment:{field_name}")

    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"invalid_quality_assessment:{field_name}")

    if len(set(value)) != len(value):
        raise ValueError(f"invalid_quality_assessment:{field_name}_duplicates")


@dataclass(frozen=True)
class MatchQualityAssessment:
    tier: QualityTier
    weight: float
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.tier) is not QualityTier:
            raise ValueError("invalid_quality_assessment:tier")

        if (
            type(self.weight) not in (int, float)
            or not math.isfinite(float(self.weight))
            or not 0.0 <= float(self.weight) <= 1.0
        ):
            raise ValueError("invalid_quality_assessment:weight")

        _validate_reason_tuple(self.reasons, "reasons")
        _validate_reason_tuple(self.missing_evidence, "missing_evidence")

        if self.tier is QualityTier.ELITE and self.missing_evidence:
            raise ValueError("invalid_quality_assessment:elite_missing_evidence")
