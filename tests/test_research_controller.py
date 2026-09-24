from haxlab.research.controller import (
    ResearchAction,
    ResearchPolicy,
    ResearchSnapshot,
    choose_next_action,
)


def test_parsing_has_priority_over_training() -> None:
    decision = choose_next_action(
        ResearchSnapshot(
            unparsed_replays=6000,
            dataset_ready=True,
            new_usable_matches_since_dataset=6000,
            new_gold_elite_matches_since_training=1000,
        ),
        ResearchPolicy(automatic_training_enabled=True),
    )

    assert decision.action == ResearchAction.PARSE_REPLAYS


def test_pending_challenger_is_evaluated_before_new_training() -> None:
    decision = choose_next_action(
        ResearchSnapshot(
            challenger_waiting_evaluation=True,
            dataset_ready=True,
            new_usable_matches_since_dataset=1000,
        ),
        ResearchPolicy(automatic_training_enabled=True),
    )

    assert decision.action == ResearchAction.EVALUATE_CHALLENGER


def test_training_requires_explicit_auto_policy() -> None:
    snapshot = ResearchSnapshot(
        dataset_ready=True,
        new_usable_matches_since_dataset=1000,
        new_gold_elite_matches_since_training=500,
    )

    assert (
        choose_next_action(snapshot, ResearchPolicy(automatic_training_enabled=False)).action
        == ResearchAction.IDLE
    )
    assert (
        choose_next_action(snapshot, ResearchPolicy(automatic_training_enabled=True)).action
        == ResearchAction.TRAIN_CHALLENGER
    )


def test_passing_challenger_is_promoted() -> None:
    decision = choose_next_action(
        ResearchSnapshot(challenger_passed_evaluation=True)
    )

    assert decision.action == ResearchAction.PROMOTE_CHALLENGER
