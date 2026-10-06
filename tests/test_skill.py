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
    ("field_name", "value"),
    [
        ("match_quality_weight", math.nan),
        ("match_quality_weight", math.inf),
        ("match_quality_weight", True),
        ("match_quality_weight", "1.0"),
        ("minutes_or_possessions_weight", math.nan),
        ("minutes_or_possessions_weight", math.inf),
        ("minutes_or_possessions_weight", False),
        ("opponent_context", math.nan),
        ("teammate_context", math.inf),
        ("state_difficulty", True),
    ],
)
def test_rejects_malformed_observation_evidence(
    field_name: str,
    value: object,
) -> None:
    kwargs = {
        "player_id": "player-a",
        "performance": PerformanceVector(retention=0.5),
        "match_quality_weight": 1.0,
        "minutes_or_possessions_weight": 10.0,
        "opponent_context": 0.0,
        "teammate_context": 0.0,
        "state_difficulty": 0.0,
    }
    kwargs[field_name] = value
    observation = SkillObservation(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=f"invalid_numeric_evidence:{field_name}"):
        estimate_player_skill_v0("player-a", [observation])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "0.5"])
def test_rejects_malformed_performance_values(value: object) -> None:
    observation = SkillObservation(
        player_id="player-a",
        performance=PerformanceVector(retention=value),  # type: ignore[arg-type]
        match_quality_weight=1.0,
        minutes_or_possessions_weight=10.0,
    )

    with pytest.raises(
        ValueError,
        match=r"invalid_numeric_evidence:performance\.retention",
    ):
        estimate_player_skill_v0("player-a", [observation])


@pytest.mark.parametrize(
    ("prior_mean", "prior_weight", "reason"),
    [
        (math.nan, 5.0, "invalid_numeric_evidence:prior_mean"),
        (0.0, math.inf, "invalid_numeric_evidence:prior_weight"),
        (0.0, True, "invalid_numeric_evidence:prior_weight"),
        (0.0, 0.0, "prior_weight_must_be_positive"),
        (0.0, -1.0, "prior_weight_must_be_positive"),
    ],
)
def test_rejects_malformed_prior(
    prior_mean: object,
    prior_weight: object,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        estimate_player_skill_v0(
            "player-a",
            [_observation(opponent=0.0)],
            prior_mean=prior_mean,  # type: ignore[arg-type]
            prior_weight=prior_weight,  # type: ignore[arg-type]
        )
