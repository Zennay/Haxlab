from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/arena-v2-evaluation-validation.yml")
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _official_action_refs() -> list[tuple[str, str]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return USES_RE.findall(text)


def test_evaluation_workflow_pins_official_actions_to_immutable_shas() -> None:
    refs = _official_action_refs()
    assert refs, "expected at least one official GitHub Action reference"
    assert [
        (action, ref)
        for action, ref in refs
        if FULL_SHA_RE.fullmatch(ref) is None
    ] == []


def test_evaluation_checkout_pin_is_the_reviewed_v4_commit() -> None:
    checkout_refs = [
        ref
        for action, ref in _official_action_refs()
        if action == "actions/checkout"
    ]
    assert checkout_refs == [EXPECTED_CHECKOUT_SHA]
