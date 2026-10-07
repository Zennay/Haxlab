import json

import pytest

from haxlab.evaluation.evidence_io import (
    load_evaluation_evidence,
    load_promotion_policy,
    parse_evaluation_evidence,
    parse_promotion_policy,
)
from haxlab.evaluation.promotion import decide_promotion


def _payload(**overrides):
    value = {
        "challenger_id": "model-2",
        "champion_id": "model-1",
        "games_vs_champion": 1000,
        "score_rate_vs_champion": 0.55,
        "score_rate_lower_bound": 0.52,
        "goal_difference_per_game": 0.2,
        "frozen_scenarios_total": 100,
        "frozen_scenarios_passed": 100,
        "regressions": [],
        "reproducible": True,
    }
    value.update(overrides)
    return value


def test_valid_payload_builds_evaluation_evidence() -> None:
    result = parse_evaluation_evidence(_payload())

    assert result.valid
    assert result.reasons == ()
    assert result.evidence is not None
    assert result.evidence.challenger_id == "model-2"
    assert result.evidence.games_vs_champion == 1000


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("challenger_id", 2, "invalid_evidence:challenger_id:not_string"),
        ("champion_id", " ", "invalid_evidence:champion_id:empty"),
        (
            "games_vs_champion",
            "1000",
            "invalid_evidence:games_vs_champion:not_integer",
        ),
        (
            "games_vs_champion",
            True,
            "invalid_evidence:games_vs_champion:not_integer",
        ),
        (
            "score_rate_vs_champion",
            "0.55",
            "invalid_evidence:score_rate_vs_champion:not_number",
        ),
        (
            "score_rate_lower_bound",
            float("nan"),
            "invalid_evidence:score_rate_lower_bound:non_finite",
        ),
        (
            "reproducible",
            "true",
            "invalid_evidence:reproducible:not_boolean",
        ),
    ],
)
def test_coercible_or_non_native_fields_fail_closed(
    field: str, value: object, reason: str
) -> None:
    result = parse_evaluation_evidence(_payload(**{field: value}))

    assert not result.valid
    assert result.evidence is None
    assert reason in result.reasons


def test_identity_strings_reject_surrounding_whitespace() -> None:
    result = parse_evaluation_evidence(
        _payload(
            challenger_id=" model-2",
            regressions=[
                {
                    "scenario": "kickoff ",
                    "severity": "critical",
                    "details": "regressed",
                }
            ],
        )
    )

    assert not result.valid
    assert (
        "invalid_evidence:challenger_id:surrounding_whitespace"
        in result.reasons
    )
    assert (
        "invalid_evidence:regressions:0:scenario:surrounding_whitespace"
        in result.reasons
    )


def test_missing_and_unexpected_fields_fail_closed() -> None:
    payload = _payload(extra="unexpected")
    del payload["games_vs_champion"]

    result = parse_evaluation_evidence(payload)

    assert not result.valid
    assert "invalid_evidence:missing:games_vs_champion" in result.reasons
    assert "invalid_evidence:unexpected:extra" in result.reasons


def test_cross_field_invariants_fail_closed() -> None:
    result = parse_evaluation_evidence(
        _payload(
            champion_id="model-2",
            score_rate_vs_champion=0.51,
            score_rate_lower_bound=0.52,
            frozen_scenarios_total=10,
            frozen_scenarios_passed=11,
        )
    )

    assert not result.valid
    assert "invalid_evidence:challenger_matches_champion" in result.reasons
    assert (
        "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"
        in result.reasons
    )
    assert (
        "invalid_evidence:frozen_scenarios_passed_exceeds_total:11>10"
        in result.reasons
    )


def test_regression_rows_require_exact_native_shape() -> None:
    result = parse_evaluation_evidence(
        _payload(
            regressions=[
                {
                    "scenario": "kickoff",
                    "severity": "critical",
                    "details": "regressed",
                },
                {
                    "scenario": "kickoff",
                    "severity": "critical",
                    "details": "regressed",
                },
                {
                    "scenario": 7,
                    "severity": "warning",
                    "details": [],
                    "extra": "nope",
                },
            ]
        )
    )

    assert not result.valid
    assert "invalid_evidence:regressions:1:duplicate" in result.reasons
    assert "invalid_evidence:regressions:2:scenario:not_string" in result.reasons
    assert "invalid_evidence:regressions:2:details:not_string" in result.reasons
    assert "invalid_evidence:regressions:2:unexpected:extra" in result.reasons


def test_regressions_must_be_json_list_not_tuple() -> None:
    result = parse_evaluation_evidence(_payload(regressions=()))

    assert not result.valid
    assert result.reasons == ("invalid_evidence:regressions:not_list",)


def test_loader_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("{", encoding="utf-8")

    result = load_evaluation_evidence(path)

    assert not result.valid
    assert result.reasons == (
        "invalid_evidence:path:unreadable_or_invalid_json",
    )


def test_loader_rejects_symlink(tmp_path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    link = tmp_path / "evidence.json"
    link.symlink_to(source)

    result = load_evaluation_evidence(link)

    assert not result.valid
    assert result.reasons == ("invalid_evidence:path:symlink",)


def test_loader_parses_regular_file(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    result = load_evaluation_evidence(path)

    assert result.valid
    assert result.evidence is not None
    assert result.evidence.champion_id == "model-1"


def _policy(**overrides):
    value = {
        "minimum_games": 500,
        "minimum_score_rate_lower_bound": 0.51,
        "minimum_scenario_pass_rate": 0.98,
        "allow_critical_regressions": False,
    }
    value.update(overrides)
    return value


def test_valid_serialized_policy_builds_policy() -> None:
    result = parse_promotion_policy(_policy())

    assert result.valid
    assert result.reasons == ()
    assert result.policy is not None
    assert result.policy.minimum_games == 500


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "minimum_games",
            "500",
            "invalid_policy:minimum_games:not_integer",
        ),
        (
            "minimum_games",
            True,
            "invalid_policy:minimum_games:not_integer",
        ),
        (
            "minimum_score_rate_lower_bound",
            "0.51",
            "invalid_policy:minimum_score_rate_lower_bound:not_number",
        ),
        (
            "minimum_scenario_pass_rate",
            float("inf"),
            "invalid_policy:minimum_scenario_pass_rate:non_finite",
        ),
        (
            "allow_critical_regressions",
            "false",
            "invalid_policy:allow_critical_regressions:not_boolean",
        ),
    ],
)
def test_serialized_policy_rejects_coercible_values(
    field: str, value: object, reason: str
) -> None:
    result = parse_promotion_policy(_policy(**{field: value}))

    assert not result.valid
    assert result.policy is None
    assert reason in result.reasons


def test_serialized_policy_requires_exact_complete_shape() -> None:
    payload = _policy(extra="unexpected")
    del payload["minimum_games"]

    result = parse_promotion_policy(payload)

    assert not result.valid
    assert "invalid_policy:missing:minimum_games" in result.reasons
    assert "invalid_policy:unexpected:extra" in result.reasons


def test_serialized_policy_rejects_out_of_range_thresholds() -> None:
    result = parse_promotion_policy(
        _policy(
            minimum_games=0,
            minimum_score_rate_lower_bound=-0.01,
            minimum_scenario_pass_rate=1.01,
        )
    )

    assert not result.valid
    assert any(
        reason.startswith("invalid_policy:minimum_games:below_minimum")
        for reason in result.reasons
    )
    assert any(
        reason.startswith(
            "invalid_policy:minimum_score_rate_lower_bound:below_minimum"
        )
        for reason in result.reasons
    )
    assert any(
        reason.startswith(
            "invalid_policy:minimum_scenario_pass_rate:above_maximum"
        )
        for reason in result.reasons
    )


def test_policy_loader_rejects_symlink(tmp_path) -> None:
    source = tmp_path / "policy-source.json"
    source.write_text(json.dumps(_policy()), encoding="utf-8")
    link = tmp_path / "policy.json"
    link.symlink_to(source)

    result = load_promotion_policy(link)

    assert not result.valid
    assert result.reasons == ("invalid_policy:path:symlink",)


def test_policy_loader_parses_regular_file(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")

    result = load_promotion_policy(path)

    assert result.valid
    assert result.policy is not None
    assert result.policy.minimum_scenario_pass_rate == 0.98


def test_strict_serialized_inputs_feed_existing_promotion_gate() -> None:
    evidence_result = parse_evaluation_evidence(_payload())
    policy_result = parse_promotion_policy(_policy())

    assert evidence_result.valid
    assert policy_result.valid
    assert evidence_result.evidence is not None
    assert policy_result.policy is not None

    decision = decide_promotion(
        evidence_result.evidence,
        policy_result.policy,
    )

    assert decision.promote
    assert decision.reasons == (
        "head_to_head_gate_passed",
        "frozen_scenarios_passed",
        "no_blocking_regressions",
        "run_reproducible",
    )


def test_strict_parser_preserves_blocking_critical_regression() -> None:
    evidence_result = parse_evaluation_evidence(
        _payload(
            regressions=[
                {
                    "scenario": "last_man_defence",
                    "severity": "critical",
                    "details": "large regression",
                }
            ]
        )
    )
    policy_result = parse_promotion_policy(_policy())

    assert evidence_result.valid
    assert policy_result.valid
    assert evidence_result.evidence is not None
    assert policy_result.policy is not None

    decision = decide_promotion(
        evidence_result.evidence,
        policy_result.policy,
    )

    assert not decision.promote
    assert any(
        reason.startswith("critical_regressions:last_man_defence")
        for reason in decision.reasons
    )


def test_non_reproducible_evidence_parses_but_cannot_promote() -> None:
    evidence_result = parse_evaluation_evidence(_payload(reproducible=False))
    policy_result = parse_promotion_policy(_policy())

    assert evidence_result.valid
    assert policy_result.valid
    assert evidence_result.evidence is not None
    assert policy_result.policy is not None

    decision = decide_promotion(
        evidence_result.evidence,
        policy_result.policy,
    )

    assert not decision.promote
    assert "run_not_reproducible" in decision.reasons


def test_evidence_loader_rejects_duplicate_json_key(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    serialized = json.dumps(_payload())
    path.write_text(
        serialized[:-1] + ', "reproducible": false}',
        encoding="utf-8",
    )

    result = load_evaluation_evidence(path)

    assert not result.valid
    assert result.reasons == (
        "invalid_evidence:path:duplicate_json_key:reproducible",
    )


def test_policy_loader_rejects_duplicate_json_key(tmp_path) -> None:
    path = tmp_path / "policy.json"
    serialized = json.dumps(_policy())
    path.write_text(
        serialized[:-1] + ', "minimum_games": 1}',
        encoding="utf-8",
    )

    result = load_promotion_policy(path)

    assert not result.valid
    assert result.reasons == (
        "invalid_policy:path:duplicate_json_key:minimum_games",
    )


class _ExplodingString(str):
    def strip(self, chars=None):
        raise AssertionError("custom strip must not run at evidence IO boundary")


class _ExplodingFloat(float):
    def __float__(self) -> float:
        raise AssertionError("custom __float__ must not run at evidence IO boundary")


class _ExplodingInt(int):
    def __int__(self) -> int:
        raise AssertionError("custom __int__ must not run at evidence IO boundary")


class _DictSubclass(dict):
    pass


class _ListSubclass(list):
    pass


def test_evidence_parser_rejects_scalar_subclasses_without_conversion_hooks() -> None:
    payload = _valid_evidence_payload()
    payload["challenger_id"] = _ExplodingString("challenger")
    payload["score_rate_vs_champion"] = _ExplodingFloat(0.6)
    payload["games_vs_champion"] = _ExplodingInt(500)

    result = parse_evaluation_evidence(payload)

    assert not result.valid
    assert result.evidence is None
    assert "invalid_evidence:challenger_id:not_string" in result.reasons
    assert "invalid_evidence:score_rate_vs_champion:not_number" in result.reasons
    assert "invalid_evidence:games_vs_champion:not_integer" in result.reasons


def test_policy_parser_rejects_numeric_subclasses_without_conversion_hooks() -> None:
    payload = _valid_policy_payload()
    payload["minimum_games"] = _ExplodingInt(500)
    payload["minimum_score_rate_lower_bound"] = _ExplodingFloat(0.51)

    result = parse_promotion_policy(payload)

    assert not result.valid
    assert result.policy is None
    assert "invalid_policy:minimum_games:not_integer" in result.reasons
    assert "invalid_policy:minimum_score_rate_lower_bound:not_number" in result.reasons


def test_evidence_parser_requires_exact_json_container_types() -> None:
    payload = _DictSubclass(_valid_evidence_payload())

    result = parse_evaluation_evidence(payload)

    assert not result.valid
    assert result.reasons == ("invalid_evidence:payload:not_object",)


def test_regressions_require_exact_json_list_type() -> None:
    payload = _valid_evidence_payload()
    payload["regressions"] = _ListSubclass(payload["regressions"])

    result = parse_evaluation_evidence(payload)

    assert not result.valid
    assert "invalid_evidence:regressions:not_list" in result.reasons
