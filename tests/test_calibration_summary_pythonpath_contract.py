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


def test_calibration_summary_import_is_bound_to_exact_checkout() -> None:
    script = _step_script("Summarize calibration evidence")

    command = "/opt/haxlab/.venv/bin/python - <<'PY'"
    python_pos = script.index(command)
    command_prefix = script[:python_pos]

    assert "sudo -u haxlab env \\" in command_prefix
    assert 'PYTHONPATH="$GITHUB_WORKSPACE/src" \\' in command_prefix
    assert 'ARENA_OUT="$ARENA_OUT" \\' in command_prefix
    assert 'GITHUB_SHA="$GITHUB_SHA" \\' in command_prefix
    assert (
        command_prefix.rindex('PYTHONPATH="$GITHUB_WORKSPACE/src"')
        > command_prefix.rindex("sudo -u haxlab env")
    )
    assert (
        "from haxlab.evaluation.calibration_gate import decide_calibration_gate"
        in script[python_pos:]
    )


def test_expensive_rollout_step_already_uses_exact_checkout_pythonpath() -> None:
    script = _step_script("Run full frozen paired calibration batch")

    assert 'PYTHONPATH="$GITHUB_WORKSPACE/src"' in script
    assert "-m haxlab.evaluation.calibration_resume" in script
    assert "-m haxlab.evaluation.closed_loop_arena" in script
