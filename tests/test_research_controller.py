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


def test_string_passed_evaluation_flag_cannot_trigger_promotion() -> None:
    decision = choose_next_action(
        ResearchSnapshot(challenger_passed_evaluation="false")
    )

    assert decision.action == ResearchAction.IDLE
    assert "invalid_research_state" in decision.reasons
    assert "snapshot:challenger_passed_evaluation:not_boolean" in decision.reasons


def test_invalid_snapshot_counters_fail_closed() -> None:
    for snapshot, expected in (
        (
            ResearchSnapshot(unparsed_replays=-1),
            "snapshot:unparsed_replays:negative",
        ),
        (
            ResearchSnapshot(matches_needing_features=True),
            "snapshot:matches_needing_features:not_native_integer",
        ),
    ):
        decision = choose_next_action(snapshot)

        assert decision.action == ResearchAction.IDLE
        assert expected in decision.reasons


def test_malformed_training_policy_cannot_enable_training() -> None:
    snapshot = ResearchSnapshot(
        dataset_ready=True,
        new_usable_matches_since_dataset=1000,
    )
    decision = choose_next_action(
        snapshot,
        ResearchPolicy(automatic_training_enabled="true"),
    )

    assert decision.action == ResearchAction.IDLE
    assert "policy:automatic_training_enabled:not_boolean" in decision.reasons


def test_negative_policy_threshold_fails_closed() -> None:
    decision = choose_next_action(
        ResearchSnapshot(dataset_dirty=True),
        ResearchPolicy(minimum_new_usable_matches_for_dataset=-1),
    )

    assert decision.action == ResearchAction.IDLE
    assert "policy:minimum_new_usable_matches_for_dataset:negative" in decision.reasons


def test_contradictory_challenger_state_cannot_promote() -> None:
    decision = choose_next_action(
        ResearchSnapshot(
            challenger_waiting_evaluation=True,
            challenger_passed_evaluation=True,
        )
    )

    assert decision.action == ResearchAction.IDLE
    assert "snapshot:challenger_state:contradictory" in decision.reasons


def test_non_snapshot_or_policy_objects_fail_closed() -> None:
    snapshot_decision = choose_next_action(object())
    policy_decision = choose_next_action(ResearchSnapshot(), object())

    assert snapshot_decision.action == ResearchAction.IDLE
    assert snapshot_decision.reasons == (
        "invalid_research_state",
        "snapshot:not_research_snapshot",
    )
    assert policy_decision.action == ResearchAction.IDLE
    assert policy_decision.reasons == (
        "invalid_research_state",
        "policy:not_research_policy",
    )


def test_training_cannot_overlap_challenger_evaluation_state() -> None:
    for snapshot in (
        ResearchSnapshot(
            training_active=True,
            challenger_waiting_evaluation=True,
        ),
        ResearchSnapshot(
            training_active=True,
            challenger_passed_evaluation=True,
        ),
        ResearchSnapshot(
            training_active=True,
            rejected_challenger_needs_failure_mining=True,
        ),
    ):
        decision = choose_next_action(snapshot)

        assert decision.action == ResearchAction.IDLE
        assert (
            "snapshot:training_and_challenger_state:contradictory"
            in decision.reasons
        )

