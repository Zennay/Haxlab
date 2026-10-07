from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from inspect import getsource
from textwrap import dedent

import pytest

from haxlab.evaluation.models import (
    EvaluationEvidence,
    PromotionDecision,
    PromotionPolicy,
    Regression,
)
from haxlab.evaluation.promotion import decide_promotion


EXPECTED_REGRESSION_FIELDS = (
    "scenario",
    "severity",
    "details",
)
EXPECTED_EVIDENCE_FIELDS = (
    "challenger_id",
    "champion_id",
    "games_vs_champion",
    "score_rate_vs_champion",
    "score_rate_lower_bound",
    "goal_difference_per_game",
    "frozen_scenarios_total",
    "frozen_scenarios_passed",
    "regressions",
    "reproducible",
)
EXPECTED_POLICY_FIELDS = (
    "minimum_games",
    "minimum_score_rate_lower_bound",
    "minimum_scenario_pass_rate",
    "allow_critical_regressions",
)
EXPECTED_DECISION_FIELDS = (
    "promote",
    "reasons",
)


def _field_names(model: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(model))


def _attribute_reads(argument_name: str) -> set[str]:
    tree = ast.parse(dedent(getsource(decide_promotion)))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == argument_name
    }


def test_evaluation_schema_changes_require_explicit_gate_review() -> None:
    assert _field_names(Regression) == EXPECTED_REGRESSION_FIELDS
    assert _field_names(EvaluationEvidence) == EXPECTED_EVIDENCE_FIELDS
    assert _field_names(PromotionPolicy) == EXPECTED_POLICY_FIELDS
    assert _field_names(PromotionDecision) == EXPECTED_DECISION_FIELDS


def test_every_evidence_and_policy_field_is_consumed_by_promotion_gate() -> None:
    evidence_reads = _attribute_reads("evidence")
    policy_reads = _attribute_reads("policy")

    assert set(EXPECTED_EVIDENCE_FIELDS) <= evidence_reads
    assert set(EXPECTED_POLICY_FIELDS) <= policy_reads


@pytest.mark.parametrize(
    ("model", "attribute", "value"),
    (
        (Regression("kickoff", "critical"), "severity", "warning"),
        (
            EvaluationEvidence(
                challenger_id="candidate",
                champion_id="champion",
                games_vs_champion=500,
                score_rate_vs_champion=0.55,
                score_rate_lower_bound=0.52,
            ),
            "games_vs_champion",
            1,
        ),
        (PromotionPolicy(), "minimum_games", 1),
        (PromotionDecision(False, ("blocked",)), "promote", True),
    ),
)
def test_evaluation_contract_objects_are_immutable(
    model: object,
    attribute: str,
    value: object,
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(model, attribute, value)


def test_default_policy_and_evidence_collections_are_fail_closed_and_immutable() -> None:
    evidence = EvaluationEvidence(
        challenger_id="candidate",
        champion_id="champion",
        games_vs_champion=0,
        score_rate_vs_champion=0.0,
        score_rate_lower_bound=0.0,
    )
    policy = PromotionPolicy()

    assert evidence.regressions == ()
    assert isinstance(evidence.regressions, tuple)
    assert evidence.reproducible is False
    assert type(policy.minimum_games) is int
    assert policy.minimum_games > 0
    assert 0.0 <= policy.minimum_score_rate_lower_bound <= 1.0
    assert 0.0 <= policy.minimum_scenario_pass_rate <= 1.0
    assert policy.allow_critical_regressions is False
