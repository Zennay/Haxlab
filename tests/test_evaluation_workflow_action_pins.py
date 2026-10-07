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


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _official_action_refs() -> list[tuple[str, str]]:
    return USES_RE.findall(_workflow_text())


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


def test_evaluation_workflow_verifies_exact_checked_out_sha_before_tests() -> None:
    text = _workflow_text()
    checkout = text.index("name: Checkout exact validation head")
    verify = text.index("name: Verify exact validation head")
    environment = text.index("name: Create isolated test environment")

    assert checkout < verify < environment
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text
