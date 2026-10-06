from pathlib import Path


WORKFLOW = Path(".github/workflows/ci.yml")


def test_haxlab_ci_checks_out_exact_event_source_sha() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    source_expr = "${{ github.event.pull_request.head.sha || github.sha }}"
    assert "name: Checkout exact event source SHA" in text
    assert f"ref: {source_expr}" in text
    assert "clean: true" in text


def test_haxlab_ci_verifies_checked_out_sha_before_tests() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    source_expr = "${{ github.event.pull_request.head.sha || github.sha }}"
    assert "name: Verify exact source checkout" in text
    assert f"EXPECTED_SHA: {source_expr}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text
    assert "CI provenance mismatch" in text

    verify_index = text.index("name: Verify exact source checkout")
    test_env_index = text.index("name: Create isolated test environment")
    assert verify_index < test_env_index
