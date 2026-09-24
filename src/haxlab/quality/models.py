from __future__ import annotations

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


@dataclass(frozen=True)
class MatchQualityAssessment:
    tier: QualityTier
    weight: float
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
