from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/multisource-suite-integrity-validation.yml")
PINNED_CHECKOUT = "11d5960a326750d5838078e36cf38b85af677262"
SOURCE_SHA_EXPR = "${{ github.event.pull_request.head.sha || github.sha }}"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_multisource_suite_workflow_pins_checkout_to_immutable_commit() -> None:
    text = _workflow_text()

    assert f"uses: actions/checkout@{PINNED_CHECKOUT}" in text
    assert "uses: actions/checkout@v4" not in text
    assert "persist-credentials: false" in text


def test_multisource_suite_workflow_binds_and_verifies_exact_source_sha() -> None:
    text = _workflow_text()

    assert f"ref: {SOURCE_SHA_EXPR}" in text
    assert f"EXPECTED_SHA: {SOURCE_SHA_EXPR}" in text
    assert 'actual_sha="$(git rev-parse HEAD)"' in text
    assert 'if [[ "$actual_sha" != "$EXPECTED_SHA" ]]; then' in text
    assert "MULTISOURCE_SUITE_INTEGRITY_EXACT_SHA=$actual_sha" in text


def test_pull_request_proof_uses_real_head_not_synthetic_merge_sha() -> None:
    text = _workflow_text()

    assert "pull_request:" in text
    assert "feature/arena-v2-current-main-20261004" in text
    assert "github.event.pull_request.head.sha || github.sha" in text
    assert "github.event.pull_request.number || github.ref" in text


def test_provenance_contract_runs_before_suite_regressions() -> None:
    text = _workflow_text()

    contract_step = text.index("- name: Run workflow provenance contract")
    suite_step = text.index("- name: Run frozen suite and adjacent regressions")

    assert contract_step < suite_step
    assert "tests/test_multisource_suite_workflow_provenance_contract.py" in text


def test_new_validation_branch_is_push_proof_triggered() -> None:
    text = _workflow_text()

    assert "validation/multisource-suite-workflow-provenance-20261007" in text
