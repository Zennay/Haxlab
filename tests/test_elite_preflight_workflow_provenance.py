from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "elite-gate-preflight-integrity-validation.yml"
)
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "elite-preflight-workflow-provenance-proof.yml"
)
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _official_refs(path: Path = WORKFLOW) -> list[tuple[str, str]]:
    return USES_RE.findall(_text(path))


def test_elite_preflight_workflow_pins_official_actions() -> None:
    refs = _official_refs()
    assert refs, "expected at least one official GitHub Action reference"
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]


def test_elite_preflight_verifies_exact_head_before_environment() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact validation head")
    verify = text.index("name: Verify exact validation head")
    environment = text.index("name: Create isolated test environment")
    regressions = text.index("name: Run preflight and adjacent evaluation regressions")

    assert checkout < verify < environment < regressions
    assert "ref: ${{ github.sha }}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text


def test_elite_preflight_preserves_execution_scope() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 20" in text
    assert "validation/elite-gate-preflight-numeric-integrity-20261006" in text
    assert "tests/test_elite_gate_preflight.py" in text
    assert "tests/test_promotion.py" in text
    assert "tests/test_closed_loop_arena.py" in text


def test_manual_proof_is_immutable_and_live_head_bound() -> None:
    workflow = _text(PROOF_WORKFLOW)
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert "ref: ${{ github.sha }}" in workflow
    assert "clean: true" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$HEAD_SHA" = "${GITHUB_SHA}"' in workflow
    assert 'git ls-remote --exit-code origin "${GITHUB_REF}"' in workflow
    assert 'if [ "$LIVE_SHA" != "${GITHUB_SHA}" ]; then' in workflow
