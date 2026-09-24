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
