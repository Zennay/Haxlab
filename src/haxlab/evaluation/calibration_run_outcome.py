from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "haxlab-calibration-run-outcome-v1"
GATE_REJECTION_MARKER = "frozen policy validation failed:"

PREP_STEPS = (
    "Checkout exact event SHA",
    "Checkout frozen Candidate D source",
    "Validate Arena v2 implementation",
    "Prepare calibration inputs",
)
BATCH_STEP = "Run full frozen paired calibration batch"
SUMMARY_STEP = "Summarize calibration evidence"
POINTER_STEP = "Verify champion pointers unchanged"
EVIDENCE_STEPS = (
    "Collect calibration evidence",
    "Upload calibration evidence",
)

TERMINAL_CONCLUSIONS = {
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "action_required",
    "startup_failure",
    "stale",
}
INTERRUPTION_CONCLUSIONS = {"cancelled", "timed_out", "stale"}
FAILURE_CONCLUSIONS = {"failure", "action_required", "startup_failure"}


def _reason(code: str, detail: str | None = None) -> str:
    return code if detail is None else f"{code}:{detail}"


def classify_calibration_job(
    payload: Any,
    *,
    job_log: str | None = None,
) -> dict[str, Any]:
    """Classify GitHub job semantics without treating interruption as model rejection.

    The contract is diagnostic only. promotion_evidence_valid is true only for a
    fully successful calibration job. A deterministic calibration-gate rejection
    additionally requires the explicit gate marker from the job log. That marker
    does not prove that one particular model was rejected because calibration
    sanity also covers the identity and negative controls.
    """

    reasons: list[str] = []
    if not isinstance(payload, dict):
        return _result(
            run_id=None,
            job_id=None,
            classification="invalid",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=("job:not_object",),
        )

    if job_log is not None and not isinstance(job_log, str):
        reasons.append("job_log:not_string")

    run_id = payload.get("run_id")
    job_id = payload.get("id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        reasons.append("run_id:not_positive_native_integer")
        run_id = None
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        reasons.append("job_id:not_positive_native_integer")
        job_id = None

    if payload.get("name") != "calibrate":
        reasons.append(_reason("job:name:mismatch", repr(payload.get("name"))))

    status = payload.get("status")
    conclusion = payload.get("conclusion")
    if status not in {"queued", "in_progress", "completed"}:
        reasons.append(_reason("job:status:invalid", repr(status)))
    if conclusion is not None and conclusion not in TERMINAL_CONCLUSIONS:
        reasons.append(_reason("job:conclusion:invalid", repr(conclusion)))
    if status == "completed" and conclusion is None:
        reasons.append("job:completed_without_conclusion")
    if status != "completed" and conclusion is not None:
        reasons.append("job:nonterminal_with_conclusion")

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        reasons.append("steps:not_list")
        raw_steps = []

    steps: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_steps):
        if not isinstance(row, dict):
            reasons.append(f"steps:{index}:not_object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            reasons.append(f"steps:{index}:name:invalid")
            continue
        if name in steps:
            reasons.append(f"steps:duplicate:{name}")
            continue
        step_status = row.get("status")
        step_conclusion = row.get("conclusion")
        if step_status not in {"queued", "in_progress", "completed"}:
            reasons.append(f"steps:{name}:status:invalid:{step_status!r}")
        if step_conclusion is not None and step_conclusion not in TERMINAL_CONCLUSIONS:
            reasons.append(f"steps:{name}:conclusion:invalid:{step_conclusion!r}")
        if step_status == "completed" and step_conclusion is None:
            reasons.append(f"steps:{name}:completed_without_conclusion")
        if step_status != "completed" and step_conclusion is not None:
            reasons.append(f"steps:{name}:nonterminal_with_conclusion")
        steps[name] = row

    required = (*PREP_STEPS, BATCH_STEP, SUMMARY_STEP, POINTER_STEP, *EVIDENCE_STEPS)
    for name in required:
        if name not in steps:
            reasons.append(f"steps:missing:{name}")

    if reasons:
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="invalid",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=tuple(reasons),
        )

    if status != "completed":
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="incomplete",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"job:not_terminal:{status}",),
        )

    def step_conclusion(name: str) -> str | None:
        return steps[name].get("conclusion")

    failed_prep = [
        name for name in PREP_STEPS if step_conclusion(name) != "success"
    ]
    if failed_prep:
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="validation_failed",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=tuple(
                f"preflight:{name}:{step_conclusion(name)}" for name in failed_prep
            ),
        )

    batch_conclusion = step_conclusion(BATCH_STEP)
    if batch_conclusion in INTERRUPTION_CONCLUSIONS:
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="interrupted",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"batch:{batch_conclusion}",),
        )
    if batch_conclusion != "success":
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="execution_failed",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"batch:{batch_conclusion}",),
        )

    summary_conclusion = step_conclusion(SUMMARY_STEP)
    if summary_conclusion in FAILURE_CONCLUSIONS:
        if isinstance(job_log, str) and GATE_REJECTION_MARKER in job_log:
            return _result(
                run_id=run_id,
                job_id=job_id,
                classification="evaluation_gate_rejected",
                promotion_evidence_valid=False,
                gate_rejection=True,
                model_rejection=False,
                reasons=("summary:gate_rejected",),
            )
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="summary_failed",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"summary:{summary_conclusion}:no_gate_marker",),
        )
    if summary_conclusion in INTERRUPTION_CONCLUSIONS:
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="interrupted",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"summary:{summary_conclusion}",),
        )
    if summary_conclusion != "success":
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="incomplete",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"summary:{summary_conclusion}",),
        )

    downstream = (POINTER_STEP, *EVIDENCE_STEPS)
    downstream_failures = [
        f"{name}:{step_conclusion(name)}"
        for name in downstream
        if step_conclusion(name) != "success"
    ]
    if downstream_failures:
        interrupted = any(
            step_conclusion(name) in INTERRUPTION_CONCLUSIONS
            for name in downstream
        )
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="interrupted" if interrupted else "evidence_failed",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=tuple(downstream_failures),
        )

    if conclusion != "success":
        return _result(
            run_id=run_id,
            job_id=job_id,
            classification="invalid",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"job:conclusion_inconsistent:{conclusion}",),
        )

    return _result(
        run_id=run_id,
        job_id=job_id,
        classification="green",
        promotion_evidence_valid=True,
        model_rejection=False,
        reasons=(),
    )


def _result(
    *,
    run_id: int | None,
    job_id: int | None,
    classification: str,
    promotion_evidence_valid: bool,
    model_rejection: bool = False,
    gate_rejection: bool = False,
    reasons: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "job_id": job_id,
        "classification": classification,
        "promotion_evidence_valid": promotion_evidence_valid,
        "gate_rejection": gate_rejection,
        "model_rejection": model_rejection,
        "reasons": list(reasons),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify one GitHub calibration job without conflating runner "
            "interruption with model rejection."
        )
    )
    parser.add_argument("job_json", type=Path)
    parser.add_argument(
        "--job-log",
        type=Path,
        help="optional decoded GitHub job log used only for explicit gate-rejection proof",
    )
    parser.add_argument(
        "--require-green",
        action="store_true",
        help="exit non-zero unless the complete calibration job is green",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.job_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = _result(
            run_id=None,
            job_id=None,
            classification="invalid",
            promotion_evidence_valid=False,
            model_rejection=False,
            reasons=(f"job_json:unreadable:{type(exc).__name__}",),
        )
    else:
        job_log: str | None = None
        if args.job_log is not None:
            try:
                job_log = args.job_log.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                result = _result(
                    run_id=None,
                    job_id=None,
                    classification="invalid",
                    promotion_evidence_valid=False,
                    model_rejection=False,
                    reasons=(f"job_log:unreadable:{type(exc).__name__}",),
                )
            else:
                result = classify_calibration_job(payload, job_log=job_log)
        else:
            result = classify_calibration_job(payload)

    print(json.dumps(result, sort_keys=True))
    if result["classification"] == "invalid":
        return 1
    if args.require_green and not result["promotion_evidence_valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
