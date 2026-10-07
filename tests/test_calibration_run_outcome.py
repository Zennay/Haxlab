from __future__ import annotations

import json

from haxlab.evaluation.calibration_run_outcome import (
    BATCH_STEP,
    EVIDENCE_STEPS,
    MODEL_REJECTION_MARKER,
    POINTER_STEP,
    PREP_STEPS,
    SCHEMA,
    SUMMARY_STEP,
    classify_calibration_job,
    main,
)


def _step(name: str, conclusion: str, *, status: str = "completed") -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "number": 1,
    }


def _job(
    *,
    job_conclusion: str = "success",
    batch: str = "success",
    summary: str = "success",
    pointer: str = "success",
    collect: str = "success",
    upload: str = "success",
) -> dict[str, object]:
    steps = [_step(name, "success") for name in PREP_STEPS]
    steps.extend(
        (
            _step(BATCH_STEP, batch),
            _step(SUMMARY_STEP, summary),
            _step(POINTER_STEP, pointer),
            _step(EVIDENCE_STEPS[0], collect),
            _step(EVIDENCE_STEPS[1], upload),
        )
    )
    return {
        "id": 112580599451,
        "name": "calibrate",
        "status": "completed",
        "conclusion": job_conclusion,
        "run_id": 37555481447,
        "steps": steps,
    }


def test_green_job_is_the_only_promotion_valid_classification() -> None:
    result = classify_calibration_job(_job())

    assert result == {
        "schema": SCHEMA,
        "run_id": 37555481447,
        "job_id": 112580599451,
        "classification": "green",
        "promotion_evidence_valid": True,
        "model_rejection": False,
        "reasons": [],
    }


def test_runner_interrupted_batch_is_not_a_model_rejection() -> None:
    payload = _job(
        job_conclusion="failure",
        batch="cancelled",
        summary="skipped",
        pointer="skipped",
        collect="skipped",
        upload="skipped",
    )

    result = classify_calibration_job(payload)

    assert result["classification"] == "interrupted"
    assert result["promotion_evidence_valid"] is False
    assert result["model_rejection"] is False
    assert result["reasons"] == ["batch:cancelled"]


def test_failed_summary_without_gate_marker_is_not_a_model_rejection() -> None:
    result = classify_calibration_job(
        _job(
            job_conclusion="failure",
            batch="success",
            summary="failure",
            pointer="skipped",
            collect="skipped",
            upload="skipped",
        )
    )

    assert result["classification"] == "summary_failed"
    assert result["promotion_evidence_valid"] is False
    assert result["model_rejection"] is False
    assert result["reasons"] == ["summary:failure:no_gate_marker"]


def test_explicit_gate_marker_after_completed_batch_is_model_rejection() -> None:
    result = classify_calibration_job(
        _job(
            job_conclusion="failure",
            batch="success",
            summary="failure",
            pointer="skipped",
            collect="skipped",
            upload="skipped",
        ),
        job_log=f"prefix {MODEL_REJECTION_MARKER} candidate_d_failed suffix",
    )

    assert result["classification"] == "evaluation_rejected"
    assert result["promotion_evidence_valid"] is False
    assert result["model_rejection"] is True
    assert result["reasons"] == ["summary:gate_rejected"]


def test_preflight_failure_is_not_a_model_rejection() -> None:
    payload = _job(
        job_conclusion="failure",
        batch="skipped",
        summary="skipped",
        pointer="skipped",
        collect="skipped",
        upload="skipped",
    )
    payload["steps"][2]["conclusion"] = "failure"

    result = classify_calibration_job(payload)

    assert result["classification"] == "validation_failed"
    assert result["model_rejection"] is False
    assert result["reasons"] == [
        "preflight:Validate Arena v2 implementation:failure"
    ]


def test_batch_execution_failure_is_not_promoted_to_model_rejection() -> None:
    result = classify_calibration_job(
        _job(
            job_conclusion="failure",
            batch="failure",
            summary="skipped",
            pointer="skipped",
            collect="skipped",
            upload="skipped",
        ),
        job_log=f"{MODEL_REJECTION_MARKER} must_be_ignored_before_summary",
    )

    assert result["classification"] == "execution_failed"
    assert result["model_rejection"] is False
    assert result["reasons"] == ["batch:failure"]


def test_evidence_collection_failure_does_not_invalidate_model_semantics() -> None:
    result = classify_calibration_job(
        _job(
            job_conclusion="failure",
            batch="success",
            summary="success",
            pointer="success",
            collect="failure",
            upload="skipped",
        )
    )

    assert result["classification"] == "evidence_failed"
    assert result["promotion_evidence_valid"] is False
    assert result["model_rejection"] is False


def test_duplicate_required_step_fails_closed() -> None:
    payload = _job()
    payload["steps"].append(_step(BATCH_STEP, "success"))

    result = classify_calibration_job(payload)

    assert result["classification"] == "invalid"
    assert "steps:duplicate:Run full frozen paired calibration batch" in result["reasons"]


def test_bool_ids_fail_closed_instead_of_being_accepted_as_integers() -> None:
    payload = _job()
    payload["run_id"] = True
    payload["id"] = False

    result = classify_calibration_job(payload)

    assert result["classification"] == "invalid"
    assert result["run_id"] is None
    assert result["job_id"] is None
    assert "run_id:not_positive_native_integer" in result["reasons"]
    assert "job_id:not_positive_native_integer" in result["reasons"]


def test_nonterminal_job_cannot_claim_a_terminal_conclusion() -> None:
    payload = _job()
    payload["status"] = "in_progress"

    result = classify_calibration_job(payload)

    assert result["classification"] == "invalid"
    assert "job:nonterminal_with_conclusion" in result["reasons"]


def test_non_string_log_fails_closed() -> None:
    result = classify_calibration_job(_job(), job_log=object())

    assert result["classification"] == "invalid"
    assert "job_log:not_string" in result["reasons"]


def test_cli_require_green_distinguishes_valid_diagnostic_from_green_gate(
    tmp_path,
    capsys,
) -> None:
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            _job(
                job_conclusion="failure",
                batch="cancelled",
                summary="skipped",
                pointer="skipped",
                collect="skipped",
                upload="skipped",
            )
        ),
        encoding="utf-8",
    )

    assert main([str(path)]) == 0
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["classification"] == "interrupted"

    assert main([str(path), "--require-green"]) == 2
    strict = json.loads(capsys.readouterr().out)
    assert strict["promotion_evidence_valid"] is False


def test_cli_job_log_can_prove_explicit_gate_rejection(tmp_path, capsys) -> None:
    job_path = tmp_path / "job.json"
    log_path = tmp_path / "job.log"
    job_path.write_text(
        json.dumps(
            _job(
                job_conclusion="failure",
                batch="success",
                summary="failure",
                pointer="skipped",
                collect="skipped",
                upload="skipped",
            )
        ),
        encoding="utf-8",
    )
    log_path.write_text(
        f"error: {MODEL_REJECTION_MARKER} candidate_d_failed\n",
        encoding="utf-8",
    )

    assert main([str(job_path), "--job-log", str(log_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["classification"] == "evaluation_rejected"
    assert result["model_rejection"] is True


def test_cli_malformed_json_is_invalid(tmp_path, capsys) -> None:
    path = tmp_path / "job.json"
    path.write_text("{not-json", encoding="utf-8")

    assert main([str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["classification"] == "invalid"
    assert result["promotion_evidence_valid"] is False
