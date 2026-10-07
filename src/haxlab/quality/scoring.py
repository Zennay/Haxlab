from __future__ import annotations

import math

from haxlab.quality.models import (
    MatchQualityAssessment,
    MatchQualityEvidence,
    QualityTier,
)


def _is_native_int(value: object) -> bool:
    return type(value) is int


def _is_finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _invalid_evidence_reasons(evidence: MatchQualityEvidence) -> tuple[str, ...]:
    invalid: list[str] = []

    if type(evidence.replay_valid) is not bool:
        invalid.append("invalid_replay_valid")

    if evidence.duration_seconds is not None and (
        not _is_finite_number(evidence.duration_seconds)
        or float(evidence.duration_seconds) < 0.0
    ):
        invalid.append("invalid_duration_seconds")

    if evidence.expected_player_count is not None and (
        not _is_native_int(evidence.expected_player_count)
        or evidence.expected_player_count <= 0
    ):
        invalid.append("invalid_expected_player_count")

    if evidence.observed_player_count is not None and (
        not _is_native_int(evidence.observed_player_count)
        or evidence.observed_player_count < 0
    ):
        invalid.append("invalid_observed_player_count")

    if evidence.disconnect_count is not None and (
        not _is_native_int(evidence.disconnect_count)
        or evidence.disconnect_count < 0
    ):
        invalid.append("invalid_disconnect_count")

    if evidence.activity_ratio is not None and (
        not _is_finite_number(evidence.activity_ratio)
        or not 0.0 <= float(evidence.activity_ratio) <= 1.0
    ):
        invalid.append("invalid_activity_ratio")

    if evidence.parser_completeness is not None and (
        not _is_finite_number(evidence.parser_completeness)
        or not 0.0 <= float(evidence.parser_completeness) <= 1.0
    ):
        invalid.append("invalid_parser_completeness")

    if (
        evidence.stadium_supported is not None
        and type(evidence.stadium_supported) is not bool
    ):
        invalid.append("invalid_stadium_supported")

    if (
        evidence.has_reliable_player_identities is not None
        and type(evidence.has_reliable_player_identities) is not bool
    ):
        invalid.append("invalid_player_identity_confidence")

    return tuple(sorted(invalid))


def assess_match_quality(evidence: MatchQualityEvidence) -> MatchQualityAssessment:
    """Conservative V0 policy.

    The policy only scores evidence we actually know. Missing values remain visible
    instead of silently being interpreted as good.
    """
    if type(evidence) is not MatchQualityEvidence:
        return MatchQualityAssessment(
            tier=QualityTier.REJECTED,
            weight=0.0,
            reasons=("invalid_quality_evidence",),
            missing_evidence=(),
        )

    invalid = _invalid_evidence_reasons(evidence)
    if invalid:
        return MatchQualityAssessment(
            tier=QualityTier.REJECTED,
            weight=0.0,
            reasons=invalid,
            missing_evidence=(),
        )

    reasons: list[str] = []
    missing: list[str] = []

    if not evidence.replay_valid:
        return MatchQualityAssessment(
            tier=QualityTier.REJECTED,
            weight=0.0,
            reasons=("invalid_replay",),
            missing_evidence=(),
        )

    score = 0.5

    if evidence.duration_seconds is None:
        missing.append("duration_seconds")
    elif evidence.duration_seconds < 60:
        reasons.append("very_short_match")
        score -= 0.30
    elif evidence.duration_seconds >= 240:
        reasons.append("meaningful_duration")
        score += 0.10

    if (
        evidence.expected_player_count is not None
        and evidence.observed_player_count is not None
    ):
        if evidence.observed_player_count < evidence.expected_player_count:
            reasons.append("incomplete_player_count")
            score -= 0.20
        elif evidence.observed_player_count > evidence.expected_player_count:
            reasons.append("excess_player_count")
            score -= 0.20
        else:
            reasons.append("expected_player_count_present")
            score += 0.05
    else:
        missing.append("player_count")

    if evidence.disconnect_count is None:
        missing.append("disconnect_count")
    elif evidence.disconnect_count > 0:
        reasons.append("disconnects_present")
        score -= min(0.25, 0.08 * evidence.disconnect_count)
    else:
        reasons.append("no_detected_disconnects")
        score += 0.05

    if evidence.activity_ratio is None:
        missing.append("activity_ratio")
    elif evidence.activity_ratio < 0.5:
        reasons.append("low_activity")
        score -= 0.20
    elif evidence.activity_ratio >= 0.8:
        reasons.append("healthy_activity")
        score += 0.05

    if evidence.parser_completeness is None:
        missing.append("parser_completeness")
    elif evidence.parser_completeness < 0.8:
        reasons.append("low_parser_completeness")
        score -= 0.30
    elif evidence.parser_completeness >= 0.98:
        reasons.append("high_parser_completeness")
        score += 0.10

    if evidence.stadium_supported is None:
        missing.append("stadium_supported")
    elif not evidence.stadium_supported:
        reasons.append("unsupported_stadium")
        score -= 0.20

    if evidence.has_reliable_player_identities is None:
        missing.append("player_identity_confidence")
    elif not evidence.has_reliable_player_identities:
        reasons.append("uncertain_player_identity")
        score -= 0.10

    score = max(0.0, min(1.0, score))

    if score < 0.25:
        tier = QualityTier.REJECTED
    elif score < 0.45:
        tier = QualityTier.BRONZE
    elif score < 0.65:
        tier = QualityTier.SILVER
    elif score < 0.85:
        tier = QualityTier.GOLD
    else:
        tier = QualityTier.ELITE

    if missing and tier == QualityTier.ELITE:
        tier = QualityTier.GOLD
        reasons.append("elite_capped_by_missing_evidence")

    return MatchQualityAssessment(
        tier=tier,
        weight=round(score, 4),
        reasons=tuple(reasons),
        missing_evidence=tuple(sorted(set(missing))),
    )
