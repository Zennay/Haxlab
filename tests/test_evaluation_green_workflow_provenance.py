from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "evaluation-green-integration-validation.yml"
)
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "evaluation-green-workflow-provenance-proof.yml"
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


def test_evaluation_green_workflow_pins_official_actions() -> None:
    refs = _official_refs()
    assert refs, "expected at least one official GitHub Action reference"
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]


def test_exact_head_precedes_all_integration_evidence() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact integration head")
    verify = text.index("name: Verify exact integration head")
    environment = text.index("name: Create isolated validation environment")
    python_suite = text.index("name: Run full Python regression suite")
    node_suite = text.index("name: Run integrated Arena Node regressions")
    runtime = text.index("name: Validate current runtime artifacts")
    frozen = text.index("name: Validate all frozen scenario disc states")

    assert checkout < verify < environment < python_suite < node_suite < runtime < frozen
    assert "ref: ${{ github.sha }}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text


def test_evaluation_green_preserves_execution_boundary() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 45" in text
    assert "validation/evaluation-green-integration-20261006" in text
    assert ".evaluation-integration-venv/bin/pytest -q" in text
    assert "tests/test_closed_loop_arena_v2.js" in text
    assert "tests/test_elite_policy_runtime_integrity.js" in text
    assert "tests/test_replay_scenario_state_integrity.js" in text
    assert "name: Validate current runtime artifacts" in text
    assert "name: Validate all frozen scenario disc states" in text


def test_manual_proof_is_source_only_and_live_head_bound() -> None:
    workflow = _text(PROOF_WORKFLOW)
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$HEAD_SHA" = "${GITHUB_SHA}"' in workflow
    assert 'git ls-remote --exit-code origin "${GITHUB_REF}"' in workflow
    assert "/var/lib/haxlab/" not in workflow
    assert "sudo -u haxlab" not in workflow
