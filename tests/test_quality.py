from haxlab.quality.models import MatchQualityEvidence, QualityTier
from haxlab.quality.scoring import assess_match_quality


def test_missing_evidence_cannot_accidentally_be_elite() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            duration_seconds=420,
        )
    )

    assert assessment.tier != QualityTier.ELITE
    assert assessment.missing_evidence


def test_invalid_replay_is_rejected() -> None:
    assessment = assess_match_quality(MatchQualityEvidence(replay_valid=False))

    assert assessment.tier == QualityTier.REJECTED
    assert assessment.weight == 0.0



def test_invalid_quality_evidence_is_rejected() -> None:
    cases = [
        (
            MatchQualityEvidence(replay_valid=True, duration_seconds=float("nan")),
            "invalid_duration_seconds",
        ),
        (
            MatchQualityEvidence(replay_valid=True, expected_player_count=0),
            "invalid_expected_player_count",
        ),
        (
            MatchQualityEvidence(replay_valid=True, observed_player_count=-1),
            "invalid_observed_player_count",
        ),
        (
            MatchQualityEvidence(replay_valid=True, disconnect_count=-1),
            "invalid_disconnect_count",
        ),
        (
            MatchQualityEvidence(replay_valid=True, activity_ratio=1.01),
            "invalid_activity_ratio",
        ),
        (
            MatchQualityEvidence(replay_valid=True, parser_completeness=-0.01),
            "invalid_parser_completeness",
        ),
        (
            MatchQualityEvidence(replay_valid=True, stadium_supported="yes"),
            "invalid_stadium_supported",
        ),
        (
            MatchQualityEvidence(
                replay_valid=True,
                has_reliable_player_identities=1,
            ),
            "invalid_player_identity_confidence",
        ),
    ]

    for evidence, reason in cases:
        assessment = assess_match_quality(evidence)

        assert assessment.tier == QualityTier.REJECTED
        assert assessment.weight == 0.0
        assert reason in assessment.reasons


def test_quality_evidence_accepts_valid_boundary_values() -> None:
    assessment = assess_match_quality(
        MatchQualityEvidence(
            replay_valid=True,
            duration_seconds=60.0,
            expected_player_count=8,
            observed_player_count=8,
            disconnect_count=0,
            activity_ratio=1.0,
            parser_completeness=1.0,
            stadium_supported=True,
            has_reliable_player_identities=True,
        )
    )

    assert assessment.tier != QualityTier.REJECTED
    assert assessment.weight > 0.0
