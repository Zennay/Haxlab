import math

import pytest

from haxlab.quality.models import MatchQualityEvidence, QualityTier
from haxlab.quality.scoring import assess_match_quality


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _HostileMatchQualityEvidence(MatchQualityEvidence):
    def __getattribute__(self, name: str) -> object:
        if name == "replay_valid":
            raise AssertionError("quality evidence was dereferenced")
        return super().__getattribute__(name)


def test_missing_evidence_cannot_accidentally_be_elite() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            duration_seconds=420,
        )
    )

    assert assessment.tier != QualityTier.ELITE
    assert assessment.missing_evidence




def test_exact_player_count_is_rewarded_as_expected_format() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            expected_player_count=8,
            observed_player_count=8,
        )
    )

    assert "expected_player_count_present" in assessment.reasons
    assert "excess_player_count" not in assessment.reasons
    assert assessment.weight == 0.55


def test_excess_player_count_is_degraded_not_rewarded() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            expected_player_count=8,
            observed_player_count=9,
        )
    )

    assert "excess_player_count" in assessment.reasons
    assert "expected_player_count_present" not in assessment.reasons
    assert assessment.weight == 0.3


def test_invalid_replay_is_rejected() -> None:
    assessment = assess_match_quality(MatchQualityEvidence(replay_valid=False))

    assert assessment.tier == QualityTier.REJECTED
    assert assessment.weight == 0.0


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"replay_valid": 1}, "invalid_replay_valid"),
        ({"duration_seconds": math.nan}, "invalid_duration_seconds"),
        ({"duration_seconds": math.inf}, "invalid_duration_seconds"),
        ({"duration_seconds": -0.1}, "invalid_duration_seconds"),
        ({"duration_seconds": _FloatSubclass(60.0)}, "invalid_duration_seconds"),
        ({"expected_player_count": 0}, "invalid_expected_player_count"),
        ({"expected_player_count": True}, "invalid_expected_player_count"),
        ({"expected_player_count": _IntSubclass(8)}, "invalid_expected_player_count"),
        ({"observed_player_count": -1}, "invalid_observed_player_count"),
        ({"observed_player_count": 8.0}, "invalid_observed_player_count"),
        ({"disconnect_count": -1}, "invalid_disconnect_count"),
        ({"disconnect_count": False}, "invalid_disconnect_count"),
        ({"activity_ratio": 1.01}, "invalid_activity_ratio"),
        ({"activity_ratio": math.nan}, "invalid_activity_ratio"),
        ({"activity_ratio": "0.8"}, "invalid_activity_ratio"),
        ({"parser_completeness": -0.01}, "invalid_parser_completeness"),
        ({"parser_completeness": math.inf}, "invalid_parser_completeness"),
        ({"stadium_supported": "yes"}, "invalid_stadium_supported"),
        ({"stadium_supported": 1}, "invalid_stadium_supported"),
        (
            {"has_reliable_player_identities": 1},
            "invalid_player_identity_confidence",
        ),
    ],
)
def test_invalid_quality_evidence_is_rejected(
    overrides: dict[str, object],
    reason: str,
) -> None:
    values: dict[str, object] = {"replay_valid": True}
    values.update(overrides)
    evidence = MatchQualityEvidence(**values)  # type: ignore[arg-type]
    assessment = assess_match_quality(evidence)

    assert assessment.tier == QualityTier.REJECTED
    assert assessment.weight == 0.0
    assert assessment.reasons == (reason,)
    assert assessment.missing_evidence == ()


def test_invalid_quality_reasons_are_sorted_and_complete() -> None:
    evidence = MatchQualityEvidence(
        replay_valid=True,
        duration_seconds=math.nan,
        expected_player_count=0,
        parser_completeness=1.5,
        stadium_supported="yes",  # type: ignore[arg-type]
    )

    assessment = assess_match_quality(evidence)

    assert assessment.reasons == (
        "invalid_duration_seconds",
        "invalid_expected_player_count",
        "invalid_parser_completeness",
        "invalid_stadium_supported",
    )
    assert assessment.missing_evidence == ()


def test_quality_evidence_accepts_valid_boundary_values() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            duration_seconds=0.0,
            expected_player_count=1,
            observed_player_count=0,
            disconnect_count=0,
            activity_ratio=0.0,
            parser_completeness=1.0,
            stadium_supported=True,
            has_reliable_player_identities=False,
        )
    )

    assert assessment.reasons == (
        "very_short_match",
        "incomplete_player_count",
        "no_detected_disconnects",
        "low_activity",
        "high_parser_completeness",
        "uncertain_player_identity",
    )
    assert assessment.missing_evidence == ()


@pytest.mark.parametrize(
    "evidence",
    [
        object(),
        {"replay_valid": True},
        _HostileMatchQualityEvidence(replay_valid=True),
    ],
)
def test_noncanonical_quality_evidence_fails_before_attribute_access(
    evidence: object,
) -> None:
    assessment = assess_match_quality(evidence)  # type: ignore[arg-type]

    assert assessment.tier == QualityTier.REJECTED
    assert assessment.weight == 0.0
    assert assessment.reasons == ("invalid_quality_evidence",)
    assert assessment.missing_evidence == ()
