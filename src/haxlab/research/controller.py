from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchAction(StrEnum):
    IDLE = "idle"
    PARSE_REPLAYS = "parse_replays"
    REFRESH_FEATURES = "refresh_features"
    BUILD_DATASET = "build_dataset"
    TRAIN_CHALLENGER = "train_challenger"
    EVALUATE_CHALLENGER = "evaluate_challenger"
    PROMOTE_CHALLENGER = "promote_challenger"
    MINE_FAILURES = "mine_failures"


@dataclass(frozen=True)
class ResearchPolicy:
    minimum_new_usable_matches_for_dataset: int = 100
    minimum_new_usable_matches_for_training: int = 250
    minimum_new_gold_elite_matches_for_training: int = 50
    automatic_training_enabled: bool = False


@dataclass(frozen=True)
class ResearchSnapshot:
    unparsed_replays: int = 0
    matches_needing_features: int = 0
    new_usable_matches_since_dataset: int = 0
    new_gold_elite_matches_since_training: int = 0
    dataset_dirty: bool = False
    dataset_ready: bool = False
    training_active: bool = False
    challenger_waiting_evaluation: bool = False
    challenger_passed_evaluation: bool = False
    rejected_challenger_needs_failure_mining: bool = False


@dataclass(frozen=True)
class ResearchDecision:
    action: ResearchAction
    reasons: tuple[str, ...]


def choose_next_action(
    snapshot: ResearchSnapshot,
    policy: ResearchPolicy = ResearchPolicy(),
) -> ResearchDecision:
    """Choose one highest-value safe next action.

    Ordering matters: finish cheap deterministic evidence work before expensive
    training, and evaluate/promote a pending challenger before starting another.
    """
    if snapshot.challenger_passed_evaluation:
        return ResearchDecision(
            ResearchAction.PROMOTE_CHALLENGER,
            ("challenger_passed_evaluation",),
        )

    if snapshot.challenger_waiting_evaluation:
        return ResearchDecision(
            ResearchAction.EVALUATE_CHALLENGER,
            ("challenger_waiting_evaluation",),
        )

    if snapshot.rejected_challenger_needs_failure_mining:
        return ResearchDecision(
            ResearchAction.MINE_FAILURES,
            ("rejected_challenger_has_unmined_failures",),
        )

    if snapshot.unparsed_replays > 0:
        return ResearchDecision(
            ResearchAction.PARSE_REPLAYS,
            (f"unparsed_replays:{snapshot.unparsed_replays}",),
        )

    if snapshot.matches_needing_features > 0:
        return ResearchDecision(
            ResearchAction.REFRESH_FEATURES,
            (f"matches_needing_features:{snapshot.matches_needing_features}",),
        )

    enough_for_dataset = (
        snapshot.new_usable_matches_since_dataset
        >= policy.minimum_new_usable_matches_for_dataset
    )
    if snapshot.dataset_dirty and enough_for_dataset:
        return ResearchDecision(
            ResearchAction.BUILD_DATASET,
            (
                "dataset_dirty",
                f"new_usable_matches:{snapshot.new_usable_matches_since_dataset}",
            ),
        )

    if snapshot.training_active:
        return ResearchDecision(
            ResearchAction.IDLE,
            ("training_already_active",),
        )

    enough_for_training = (
        snapshot.new_usable_matches_since_dataset
        >= policy.minimum_new_usable_matches_for_training
        or snapshot.new_gold_elite_matches_since_training
        >= policy.minimum_new_gold_elite_matches_for_training
    )

    if (
        policy.automatic_training_enabled
        and snapshot.dataset_ready
        and enough_for_training
    ):
        return ResearchDecision(
            ResearchAction.TRAIN_CHALLENGER,
            (
                "automatic_training_enabled",
                "dataset_ready",
                f"new_usable_matches:{snapshot.new_usable_matches_since_dataset}",
                "new_gold_elite_matches:"
                f"{snapshot.new_gold_elite_matches_since_training}",
            ),
        )

    reasons: list[str] = ["no_high_value_action_ready"]
    if not policy.automatic_training_enabled:
        reasons.append("automatic_training_disabled")
    if not snapshot.dataset_ready:
        reasons.append("dataset_not_ready")
    if not enough_for_training:
        reasons.append("insufficient_new_training_evidence")

    return ResearchDecision(ResearchAction.IDLE, tuple(reasons))
