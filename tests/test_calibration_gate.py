from pathlib import Path

from haxlab.evaluation.calibration_gate import decide_calibration_gate


def _aggregate() -> dict:
    return {
        "champion-self": {
            "sources": 3,
            "all_structurally_valid": True,
            "behavior_passes": 3,
            "promotion_eligible_sources": 3,
        },
        "candidate-d": {
            "sources": 3,
            "all_structurally_valid": True,
            "behavior_passes": 0,
            "promotion_eligible_sources": 0,
        },
        "weak-zero": {
            "sources": 3,
            "all_structurally_valid": True,
            "behavior_passes": 0,
            "promotion_eligible_sources": 0,
        },
    }


def test_expected_calibration_controls_pass() -> None:
    decision = decide_calibration_gate(_aggregate())

    assert decision.passed
    assert decision.reasons == ()
    assert decision.sanity["all_runs_structurally_valid"]


def test_structurally_invalid_negative_control_cannot_count_as_rejection() -> None:
    aggregate = _aggregate()
    aggregate["weak-zero"]["all_structurally_valid"] = False

    decision = decide_calibration_gate(aggregate)

    assert not decision.passed
    assert "structural_evidence_invalid:weak-zero" in decision.reasons
    assert not decision.sanity["negative_control_discriminated"]


def test_structurally_invalid_candidate_d_cannot_count_as_rejection() -> None:
    aggregate = _aggregate()
    aggregate["candidate-d"]["all_structurally_valid"] = False

    decision = decide_calibration_gate(aggregate)

    assert not decision.passed
    assert "structural_evidence_invalid:candidate-d" in decision.reasons
    assert not decision.sanity["candidate_d_rejected"]


def test_source_count_mismatch_fails_closed() -> None:
    aggregate = _aggregate()
    aggregate["candidate-d"]["sources"] = 2

    decision = decide_calibration_gate(aggregate)

    assert not decision.passed
    assert "calibration_source_count_mismatch" in decision.reasons
    assert not decision.sanity["all_labels_have_three_sources"]


def test_non_integer_counts_fail_closed() -> None:
    aggregate = _aggregate()
    aggregate["weak-zero"]["promotion_eligible_sources"] = "0"

    decision = decide_calibration_gate(aggregate)

    assert not decision.passed
    assert (
        "invalid_aggregate:weak-zero:promotion_eligible_sources"
        in decision.reasons
    )


def test_malformed_aggregate_fails_closed_without_exception() -> None:
    decision = decide_calibration_gate(["bad"])  # type: ignore[arg-type]

    assert not decision.passed
    assert decision.reasons == ("invalid_calibration_aggregate",)


def test_calibration_workflow_uses_fail_closed_aggregate_gate() -> None:
    workflow = Path(
        ".github/workflows/closed-loop-arena-v2-calibration.yml"
    ).read_text(encoding="utf-8")

    assert "decide_calibration_gate" in workflow
    assert '"threshold_freeze_ready": decision.passed' in workflow
    assert '"threshold_freeze_blocker": (' in workflow
