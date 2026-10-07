from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "calibration-summary-pythonpath-validation.yml"
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "calibration-summary-action-provenance-proof.yml"
)
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
EXPECTED_GITHUB_SCRIPT_SHA = "f28e40c7f34bde8b3046d885e986cb6290c5673b"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _official_refs(path: Path = WORKFLOW) -> list[tuple[str, str]]:
    return USES_RE.findall(_text(path))


def test_calibration_summary_workflow_pins_all_official_actions() -> None:
    refs = _official_refs()
    assert refs
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]
    assert [ref for action, ref in refs if action == "actions/github-script"] == [
        EXPECTED_GITHUB_SCRIPT_SHA
    ]


def test_checkout_is_credential_free_and_exact_live_head_is_verified_first() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact validation head")
    verify = text.index("name: Verify exact live head")
    environment = text.index("name: Create isolated proof environment")
    regressions = text.index("name: Run focused and adjacent calibration regressions")

    assert checkout < verify < environment < regressions
    assert "ref: ${{ github.sha }}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert 'test "$(git rev-parse HEAD)" = "${{ github.sha }}"' in text
    assert 'git ls-remote origin "refs/heads/${GITHUB_REF_NAME}"' in text
    assert 'test "$remote_head" = "${{ github.sha }}"' in text


def test_receipt_boundary_and_existing_calibration_scope_are_preserved() -> None:
    text = _text()

    assert "permissions:\n  contents: read\n  issues: write" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 20" in text
    assert "validation/calibration-summary-pythonpath-20261007" in text
    assert "workflow_dispatch:" in text
    assert "tests/test_calibration_summary_pythonpath_contract.py" in text
    assert "tests/test_calibration_gate.py" in text
    assert "tests/test_calibration_resume.py" in text
    assert "tests/test_calibration_workflow_evidence_contract.py" in text
    assert "issue_number: 375" in text
    assert "if: always()" in text
    assert "HAXLAB_CALIBRATION_SUMMARY_PYTHONPATH_RESULT=green" in text


def test_manual_proof_is_dispatch_only_immutable_and_live_head_bound() -> None:
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


def test_manual_proof_runs_provenance_before_calibration_regressions() -> None:
    workflow = _text(PROOF_WORKFLOW)
    contract = workflow.index("name: Run provenance contract")
    regressions = workflow.index("name: Run calibration summary adjacent regressions")

    assert contract < regressions
    assert "tests/test_calibration_summary_action_provenance.py" in workflow
