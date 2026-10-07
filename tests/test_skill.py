import math

import pytest

from haxlab.skill.estimator import estimate_player_skill_v0
from haxlab.skill.models import PerformanceVector, SkillObservation


def _observation(*, opponent: float, teammate: float = 0.0) -> SkillObservation:
    return SkillObservation(
        player_id="player-a",
        performance=PerformanceVector(retention=0.5),
        match_quality_weight=1.0,
        minutes_or_possessions_weight=10.0,
        opponent_context=opponent,
        teammate_context=teammate,
        state_difficulty=0.0,
    )


def test_opponent_strength_is_only_a_bounded_context_signal() -> None:
    easy = estimate_player_skill_v0("player-a", [_observation(opponent=-1.0)])
    hard = estimate_player_skill_v0("player-a", [_observation(opponent=1.0)])

    easy_mean = easy.dimensions["retention"].mean
    hard_mean = hard.dimensions["retention"].mean

    assert hard_mean > easy_mean
    assert hard_mean - easy_mean < 0.2


def test_strong_teammates_do_not_get_credited_to_player() -> None:
    neutral = estimate_player_skill_v0(
        "player-a",
        [_observation(opponent=0.0, teammate=0.0)],
    )
    stacked = estimate_player_skill_v0(
        "player-a",
        [_observation(opponent=0.0, teammate=1.0)],
    )

    assert stacked.dimensions["retention"].mean < neutral.dimensions["retention"].mean


@pytest.mark.parametrize(
    "prior_mean",
    [math.nan, math.inf, -math.inf, True, "0.0"],
)
def test_estimator_rejects_invalid_prior_mean(prior_mean: object) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_skill_estimator:prior_mean",
    ):
        estimate_player_skill_v0(
            "player-a",
            [],
            prior_mean=prior_mean,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "prior_weight",
    [0.0, -0.01, math.nan, math.inf, -math.inf, False, "5.0"],
)
def test_estimator_rejects_invalid_prior_weight(prior_weight: object) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_skill_estimator:prior_weight",
    ):
        estimate_player_skill_v0(
            "player-a",
            [],
            prior_weight=prior_weight,  # type: ignore[arg-type]
        )


def test_estimator_accepts_current_leaderboard_prior() -> None:
    estimate = estimate_player_skill_v0(
        "player-a",
        [_observation(opponent=0.0)],
        prior_mean=0.0,
        prior_weight=8.0,
    )

    assert estimate.player_id == "player-a"
    assert estimate.dimensions["retention"].effective_weight > 0.0
