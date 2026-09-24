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
