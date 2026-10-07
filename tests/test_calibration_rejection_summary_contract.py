from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "closed-loop-arena-v2-calibration.yml"


def _step_script(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = f"      - name: {name}\n"
    start = text.index(marker) + len(marker)
    next_step = text.find("\n      - name: ", start)
    return text[start:] if next_step < 0 else text[start:next_step]


def test_rejected_calibration_persists_structured_summary_before_exit() -> None:
    script = _step_script("Summarize calibration evidence")

    summary_build = script.index("summary = {")
    ready_field = script.index('"threshold_freeze_ready": decision.passed', summary_build)
    blocker_field = script.index('"threshold_freeze_blocker": (', ready_field)
    write = script.index('(root / "calibration-summary.json").write_text(', blocker_field)
    marker = script.index('"frozen policy validation failed: "', write)
    failure_guard = script.rindex("if not decision.passed:", write, marker)

    assert summary_build < ready_field < blocker_field < write < failure_guard < marker


def test_rejected_calibration_still_exits_nonzero_after_receipt_write() -> None:
    script = _step_script("Summarize calibration evidence")

    write = script.index('(root / "calibration-summary.json").write_text(')
    fail = script.index("raise SystemExit(", write)
    completed_status = script.index(
        '/tmp/haxlab-arena-v2-calibration-latest-status.json',
        fail,
    )

    assert write < fail < completed_status


def test_always_on_evidence_path_uploads_summary_receipt_when_present() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    collect = text[text.index("name: Collect calibration evidence") :]
    upload = collect[collect.index("name: Upload calibration evidence") :]

    assert "if: always()" in collect
    assert "find \"$ARENA_OUT\" -maxdepth 1 -type f -name '*.json'" in collect
    assert "if: always()" in upload
    assert "if-no-files-found: error" in upload
