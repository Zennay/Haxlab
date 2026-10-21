from __future__ import annotations

import math

import pytest

from haxlab.quality.models import (
    MatchQualityAssessment,
    MatchQualityEvidence,
    QualityTier,
)
from haxlab.quality.scoring import assess_match_quality


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _StringSubclass(str):
    pass


def test_quality_assessment_accepts_canonical_output() -> None:
    assessment = MatchQualityAssessment(
        tier=QualityTier.GOLD,
        weight=0.8,
        reasons=("meaningful_duration",),
        missing_evidence=(),
    )

    assert assessment.weight == 0.8


@pytest.mark.parametrize(
    "weight",
    [
        math.nan,
        math.inf,
        -math.inf,
        -0.01,
        1.01,
        True,
        "0.5",
        _IntSubclass(1),
        _FloatSubclass(0.5),
    ],
)
def test_quality_assessment_rejects_invalid_weight(weight: object) -> None:
    with pytest.raises(
        ValueError,
        match="invalid_quality_assessment:weight",
    ):
        MatchQualityAssessment(
            tier=QualityTier.SILVER,
            weight=weight,  # type: ignore[arg-type]
            reasons=(),
            missing_evidence=(),
        )


def test_quality_assessment_rejects_non_native_tier() -> None:
    with pytest.raises(
        ValueError,
        match="invalid_quality_assessment:tier",
    ):
        MatchQualityAssessment(
            tier="gold",  # type: ignore[arg-type]
            weight=0.8,
            reasons=(),
            missing_evidence=(),
        )


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("reasons", ["healthy_activity"], "reasons"),
        ("reasons", ("",), "reasons"),
        ("reasons", (1,), "reasons"),
        ("reasons", (_StringSubclass("healthy_activity"),), "reasons"),
        (
            "reasons",
            ("healthy_activity", "healthy_activity"),
            "reasons_duplicates",
        ),
        ("missing_evidence", ["disconnect_count"], "missing_evidence"),
        ("missing_evidence", ("",), "missing_evidence"),
        ("missing_evidence", (1,), "missing_evidence"),
        (
            "missing_evidence",
            (_StringSubclass("disconnect_count"),),
            "missing_evidence",
        ),
        (
            "missing_evidence",
            ("disconnect_count", "disconnect_count"),
            "missing_evidence_duplicates",
        ),
    ],
)
def test_quality_assessment_rejects_malformed_reason_sets(
    field_name: str,
    value: object,
    reason: str,
) -> None:
    kwargs: dict[str, object] = {
        "tier": QualityTier.SILVER,
        "weight": 0.5,
        "reasons": (),
        "missing_evidence": (),
    }
    kwargs[field_name] = value

    with pytest.raises(
        ValueError,
        match=f"invalid_quality_assessment:{reason}",
    ):
        MatchQualityAssessment(**kwargs)  # type: ignore[arg-type]


def test_elite_assessment_cannot_carry_missing_evidence() -> None:
    with pytest.raises(
        ValueError,
        match="invalid_quality_assessment:elite_missing_evidence",
    ):
        MatchQualityAssessment(
            tier=QualityTier.ELITE,
            weight=0.9,
            reasons=("high_parser_completeness",),
            missing_evidence=("player_count",),
        )


def test_current_scoring_outputs_satisfy_assessment_contract() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            duration_seconds=300.0,
            expected_player_count=8,
            observed_player_count=8,
            disconnect_count=0,
            activity_ratio=0.9,
            parser_completeness=0.99,
            stadium_supported=True,
            has_reliable_player_identities=True,
        )
    )

    assert assessment.tier is QualityTier.ELITE
    assert assessment.weight <= 1.0
    assert assessment.missing_evidence == ()
