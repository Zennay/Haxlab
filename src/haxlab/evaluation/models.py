from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Regression:
    scenario: str
    severity: str
    details: str = ""


@dataclass(frozen=True)
class EvaluationEvidence:
    challenger_id: str
    champion_id: str
    games_vs_champion: int
    score_rate_vs_champion: float
    score_rate_lower_bound: float
    goal_difference_per_game: float | None = None
    frozen_scenarios_total: int = 0
    frozen_scenarios_passed: int = 0
    regressions: tuple[Regression, ...] = ()
    reproducible: bool = False


@dataclass(frozen=True)
class PromotionPolicy:
    minimum_games: int = 500
    minimum_score_rate_lower_bound: float = 0.51
    minimum_scenario_pass_rate: float = 0.98
    allow_critical_regressions: bool = False


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reasons: tuple[str, ...]
