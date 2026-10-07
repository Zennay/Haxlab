from pathlib import Path


WORKFLOW = Path(".github/workflows/closed-loop-arena-v2-calibration.yml")


def test_full_calibration_has_bounded_eight_hour_runtime_budget() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    calibrate = text.index("jobs:")
    batch = text.index("name: Run full frozen paired calibration batch")
    job_block = text[calibrate:batch]

    assert "timeout-minutes: 480" in job_block
    assert "timeout-minutes: 360" not in job_block
    assert "timeout-minutes: 180" not in job_block
