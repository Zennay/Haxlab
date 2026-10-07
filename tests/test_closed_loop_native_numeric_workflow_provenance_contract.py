from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/closed-loop-native-numeric-validation.yml")
PINNED_CHECKOUT = "11d5960a326750d5838078e36cf38b85af677262"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_closed_loop_numeric_workflow_pins_checkout_to_immutable_commit() -> None:
    text = _workflow_text()

    assert f"uses: actions/checkout@{PINNED_CHECKOUT}" in text
    assert "uses: actions/checkout@v4" not in text
    assert "persist-credentials: false" in text


def test_closed_loop_numeric_workflow_binds_and_verifies_github_sha() -> None:
    text = _workflow_text()

    assert "ref: ${{ github.sha }}" in text
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'actual_sha="$(git rev-parse HEAD)"' in text
    assert 'if [[ "$actual_sha" != "$EXPECTED_SHA" ]]; then' in text
    assert "CLOSED_LOOP_NATIVE_NUMERIC_EXACT_SHA=$actual_sha" in text


def test_provenance_contract_runs_before_arena_regressions() -> None:
    text = _workflow_text()

    contract_step = text.index("- name: Run workflow provenance contract")
    arena_step = text.index("- name: Run Arena and adjacent evaluation regressions")

    assert contract_step < arena_step
    assert "tests/test_closed_loop_native_numeric_workflow_provenance_contract.py" in text


def test_runner_silent_staging_preserves_existing_triggers() -> None:
    text = _workflow_text()

    assert "validation/closed-loop-native-numeric-evidence-20261006" in text
    assert "validation/closed-loop-native-numeric-workflow-provenance-20261007" not in text
    assert "workflow_dispatch:" in text
