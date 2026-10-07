from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runtime-model-integrity-validation.yml"
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "runtime-model-workflow-provenance-proof.yml"
)
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
EXPECTED_SOURCE_EXPR = "${{ github.event.pull_request.head.sha || github.sha }}"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _official_refs(path: Path = WORKFLOW) -> list[tuple[str, str]]:
    return USES_RE.findall(_text(path))


def test_runtime_model_workflow_pins_official_actions() -> None:
    refs = _official_refs()
    assert refs, "expected at least one official GitHub Action reference"
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]


def test_runtime_model_verifies_expected_source_before_any_validation() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact validation head")
    verify = text.index("name: Verify exact validation head")
    syntax = text.index("name: Validate runtime Node syntax")
    regressions = text.index("name: Run runtime model integrity regressions")
    live = text.index("name: Validate current runtime artifacts")

    assert checkout < verify < syntax < regressions < live
    assert f"ref: {EXPECTED_SOURCE_EXPR}" in text
    assert f"EXPECTED_SHA: {EXPECTED_SOURCE_EXPR}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text


def test_runtime_model_preserves_trigger_and_final_identity_evidence() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 20" in text
    assert "pull_request:" in text
    assert '"tools/elite_policy_runtime.js"' in text
    assert '"tests/test_elite_policy_runtime_integrity.js"' in text
    assert '" .github/workflows/runtime-model-integrity-validation.yml"' not in text
    assert '".github/workflows/runtime-model-integrity-validation.yml"' in text
    assert "RUNTIME_MODEL_INTEGRITY_TRIGGER_SHA=" in text
    assert 'test "$(git rev-parse HEAD)" = "' in text


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
