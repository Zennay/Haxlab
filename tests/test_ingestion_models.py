import math

import pytest

from haxlab.ingestion.matcher import match_replays_to_reports
from haxlab.ingestion.reports import parse_match_report
from haxlab.models import (
    AttachmentRef,
    ImportFailure,
    ImportManifest,
    MatchCandidate,
    MatchReport,
    ReplayFile,
)


def test_current_report_and_matcher_outputs_satisfy_model_contract() -> None:
    report = parse_match_report(
        {
            "id": "1552774764110815262",
            "timestamp": "2026-09-24T22:12:27+02:00",
            "content": (
                "MATCH REPORT SCRIM #20260924T221227749-R2\n"
                "Red Team 3 - 2 Blue Team\n"
                "Possession: red 52.34% blue 47.66%"
            ),
            "attachments": [
                {
                    "fileName": "24-09-26-22h12-aavsmko.hbr2",
                    "fileSizeBytes": 44237,
                }
            ],
        },
        channel_id="726932424172371968",
    )
    replay = ReplayFile(
        path="/raw/24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
        file_name="24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
        size_bytes=44237,
        sha256="a" * 64,
    )

    matches = match_replays_to_reports([replay], [report])

    assert len(matches) == 1
    assert matches[0].replay_sha256 == replay.sha256
    assert matches[0].message_id == report.message_id


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64, "", 123, None])
def test_replay_file_rejects_noncanonical_digest(digest: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ReplayFile(
            path="/raw/replay.hbr2",
            file_name="replay.hbr2",
            size_bytes=1,
            sha256=digest,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("size_bytes", [True, 1.5, "1", -1, None])
def test_replay_size_rejects_coercion(size_bytes: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ReplayFile(
            path="/raw/replay.hbr2",
            file_name="replay.hbr2",
            size_bytes=size_bytes,  # type: ignore[arg-type]
            sha256="a" * 64,
        )


@pytest.mark.parametrize("size_bytes", [True, 1.5, "1", -1])
def test_attachment_size_rejects_coercion_when_present(size_bytes: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        AttachmentRef(
            file_name="replay.hbr2",
            size_bytes=size_bytes,  # type: ignore[arg-type]
        )


def test_attachment_size_may_be_absent() -> None:
    assert AttachmentRef(file_name="replay.hbr2").size_bytes is None


@pytest.mark.parametrize("score", [True, 1.0, "1", -1])
def test_match_report_scores_require_native_nonnegative_integers(score: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            red_score=score,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "possession",
    [True, "52.0", -0.1, 100.1, math.nan, math.inf, -math.inf],
)
def test_match_report_possession_is_finite_percentage(possession: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            possession_red=possession,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("red_score", "blue_score"),
    [(3, None), (None, 2)],
)
def test_match_report_requires_paired_score_evidence(
    red_score: int | None,
    blue_score: int | None,
) -> None:
    with pytest.raises(ValueError, match="red_score and blue_score must be both present"):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            red_score=red_score,
            blue_score=blue_score,
        )


@pytest.mark.parametrize(
    ("possession_red", "possession_blue"),
    [(52.0, None), (None, 48.0)],
)
def test_match_report_requires_paired_possession_evidence(
    possession_red: float | None,
    possession_blue: float | None,
) -> None:
    with pytest.raises(
        ValueError,
        match="possession_red and possession_blue must be both present",
    ):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            possession_red=possession_red,
            possession_blue=possession_blue,
        )


def test_match_report_accepts_complete_score_and_possession_pairs() -> None:
    report = MatchReport(
        message_id="message-1",
        timestamp=None,
        channel_id=None,
        content="MATCH REPORT",
        red_score=3,
        blue_score=2,
        possession_red=52.34,
        possession_blue=47.66,
    )

    assert (report.red_score, report.blue_score) == (3, 2)
    assert (report.possession_red, report.possession_blue) == (52.34, 47.66)


def test_match_report_rejects_container_and_identity_drift() -> None:
    with pytest.raises(TypeError):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            attachments=[AttachmentRef(file_name="replay.hbr2")],  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError):
        MatchReport(
            message_id=" message-1 ",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
        )

    with pytest.raises(TypeError):
        MatchReport(
            message_id="message-1",
            timestamp=None,
            channel_id=None,
            content="MATCH REPORT",
            attachments=("replay.hbr2",),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("confidence", [True, "0.9", -0.1, 1.1, math.nan, math.inf])
def test_match_candidate_confidence_rejects_coercion_and_nonfinite_values(
    confidence: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        MatchCandidate(
            replay_sha256="a" * 64,
            replay_path="/raw/replay.hbr2",
            message_id="message-1",
            confidence=confidence,  # type: ignore[arg-type]
        )


def test_match_candidate_requires_canonical_unique_reason_evidence() -> None:
    with pytest.raises(ValueError):
        MatchCandidate(
            replay_sha256="a" * 64,
            replay_path="/raw/replay.hbr2",
            message_id="message-1",
            confidence=0.9,
            reasons=("filename", "filename"),
        )

    with pytest.raises(ValueError):
        MatchCandidate(
            replay_sha256="a" * 64,
            replay_path="/raw/replay.hbr2",
            message_id="message-1",
            confidence=0.9,
            reasons=(" filename ",),
        )


def test_import_failure_revalidates_after_mutation() -> None:
    failure = ImportFailure(source="channel.json", stage="discord_json", error="bad json")
    failure.stage = " discord_json "

    with pytest.raises(ValueError):
        failure.validate()


def test_import_manifest_rejects_count_invariants_and_type_coercion() -> None:
    with pytest.raises(TypeError):
        ImportManifest(replay_count=True)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        ImportManifest(
            replay_count=2,
            unique_replay_count=1,
            duplicate_replay_count=0,
        )

    with pytest.raises(ValueError):
        ImportManifest(
            replay_count=1,
            unique_replay_count=1,
            report_count=0,
            match_count=1,
        )


def test_import_manifest_binds_unmatched_evidence_to_counts() -> None:
    manifest = ImportManifest(
        replay_count=2,
        unique_replay_count=2,
        report_count=2,
        match_count=1,
        unmatched_replays=["unmatched.hbr2"],
        unmatched_reports=["message-2"],
    )
    assert manifest.unmatched_reports == ["message-2"]

    with pytest.raises(
        ValueError,
        match="unmatched_reports length must equal report_count - match_count",
    ):
        ImportManifest(
            replay_count=2,
            unique_replay_count=2,
            report_count=2,
            match_count=1,
            unmatched_replays=["unmatched.hbr2"],
            unmatched_reports=[],
        )

    with pytest.raises(
        ValueError,
        match="unmatched_replays length must not exceed unique_replay_count - match_count",
    ):
        ImportManifest(
            replay_count=2,
            unique_replay_count=2,
            report_count=1,
            match_count=1,
            unmatched_replays=["one.hbr2", "two.hbr2"],
            unmatched_reports=[],
        )


def test_import_manifest_as_dict_revalidates_unmatched_count_invariants() -> None:
    manifest = ImportManifest(
        replay_count=2,
        unique_replay_count=2,
        report_count=2,
        match_count=1,
        unmatched_replays=["unmatched.hbr2"],
        unmatched_reports=["message-2"],
    )

    manifest.unmatched_reports.clear()

    with pytest.raises(
        ValueError,
        match="unmatched_reports length must equal report_count - match_count",
    ):
        manifest.as_dict()


def test_import_manifest_as_dict_fails_closed_after_mutation() -> None:
    manifest = ImportManifest(
        replay_count=2,
        unique_replay_count=1,
        duplicate_replay_count=1,
        report_count=1,
        match_count=1,
        unmatched_replays=[],
        unmatched_reports=[],
        failures=[ImportFailure(source="bad.json", stage="discord_json", error="bad")],
    )

    payload = manifest.as_dict()
    assert payload["replay_count"] == 2
    assert payload["failures"][0]["stage"] == "discord_json"

    manifest.match_count = "1"  # type: ignore[assignment]
    with pytest.raises(TypeError):
        manifest.as_dict()


def test_import_manifest_rejects_duplicate_or_malformed_evidence() -> None:
    with pytest.raises(ValueError):
        ImportManifest(
            unmatched_replays=["a.hbr2", "a.hbr2"],
        )

    with pytest.raises(TypeError):
        ImportManifest(
            failures=[{"source": "a", "stage": "b", "error": "c"}],  # type: ignore[list-item]
        )
