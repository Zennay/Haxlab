from pathlib import Path


WORKFLOW = Path(".github/workflows/arena-v2-integration-validate.yml")


def test_manual_arena_integration_validation_requires_immutable_sha() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    dispatch = text.index("workflow_dispatch:")
    permissions = text.index("permissions:")
    dispatch_block = text[dispatch:permissions]

    assert "Exact 40-character commit SHA to validate" in dispatch_block
    assert "required: true" in dispatch_block
    assert "default:" not in dispatch_block
    assert "name: Validate immutable requested SHA" in text
    assert r"^[0-9a-f]{40}$" in text


def test_arena_integration_validation_verifies_checked_out_sha() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    expected_expr = "${{ github.event_name == 'workflow_dispatch' && inputs.ref || github.sha }}"
    assert f"ref: {expected_expr}" in text
    assert "clean: true" in text
    assert "name: Verify exact requested SHA" in text
    assert f"EXPECTED_SHA: {expected_expr}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text
    assert "Arena validation provenance mismatch" in text
