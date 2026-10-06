from pathlib import Path


CALIBRATION = Path(".github/workflows/closed-loop-arena-v2-calibration.yml")
MULTISOURCE = Path(".github/workflows/multisource-suite-v2.yml")
CI = Path(".github/workflows/ci.yml")
CANONICAL_BRANCH = "feature/arena-v2-current-main-20261004"


def _step_block(text: str, name: str) -> str:
    marker = f"- name: {name}"
    start = text.index(marker)
    next_step = text.find("\n      - name:", start + len(marker))
    if next_step == -1:
        return text[start:]
    return text[start:next_step]


def _assert_read_only_self_hosted(text: str) -> None:
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "ubuntu-latest" not in text


def test_canonical_evaluation_workflows_share_the_same_push_branch() -> None:
    calibration = CALIBRATION.read_text(encoding="utf-8")
    multisource = MULTISOURCE.read_text(encoding="utf-8")

    for text in (calibration, multisource):
        assert f"- {CANONICAL_BRANCH}" in text


def test_calibration_and_multisource_checkout_exact_event_sha() -> None:
    calibration = CALIBRATION.read_text(encoding="utf-8")
    multisource = MULTISOURCE.read_text(encoding="utf-8")

    calibration_checkout = _step_block(calibration, "Checkout exact event SHA")
    multisource_checkout = _step_block(multisource, "Checkout exact event SHA")

    for block in (calibration_checkout, multisource_checkout):
        assert "uses: actions/checkout@v4" in block
        assert "ref: ${{ github.sha }}" in block
        assert "clean: true" in block
        assert "github.ref" not in block
        assert CANONICAL_BRANCH not in block


def test_canonical_evaluation_workflows_are_read_only_and_self_hosted() -> None:
    calibration = CALIBRATION.read_text(encoding="utf-8")
    multisource = MULTISOURCE.read_text(encoding="utf-8")

    _assert_read_only_self_hosted(calibration)
    _assert_read_only_self_hosted(multisource)


def test_calibration_preserves_pointer_invariant_before_publishing_evidence() -> None:
    text = CALIBRATION.read_text(encoding="utf-8")

    summarize = text.index("name: Summarize calibration evidence")
    pointer = text.index("name: Verify champion pointers unchanged")
    collect = text.index("name: Collect calibration evidence")
    upload = text.index("name: Upload calibration evidence")

    assert summarize < pointer < collect < upload
    assert "if: always()" in _step_block(text, "Collect calibration evidence")
    upload_block = _step_block(text, "Upload calibration evidence")
    assert "uses: actions/upload-artifact@v4" in upload_block
    assert "if-no-files-found: error" in upload_block


def test_multisource_self_consistency_gate_precedes_artifact_publication() -> None:
    text = MULTISOURCE.read_text(encoding="utf-8")

    freeze = text.index("name: Freeze v2 manifest from existing immutable multisource evidence")
    verify = text.index("name: Verify frozen manifest is self-consistent")
    upload = text.index("name: Upload frozen suite manifest")

    assert freeze < verify < upload
    verify_block = _step_block(text, "Verify frozen manifest is self-consistent")
    assert "suite hash mismatch" in verify_block
    assert "expected exactly three frozen sources" in verify_block
    assert "raw-policy requirement missing" in verify_block
    assert "replay sources are not disjoint" in verify_block

    upload_block = _step_block(text, "Upload frozen suite manifest")
    assert "uses: actions/upload-artifact@v4" in upload_block
    assert "if-no-files-found: error" in upload_block


def test_full_ci_uses_exact_source_sha_and_verifies_it_before_tests() -> None:
    text = CI.read_text(encoding="utf-8")
    source_expr = "${{ github.event.pull_request.head.sha || github.sha }}"

    checkout = _step_block(text, "Checkout exact event source SHA")
    verify = _step_block(text, "Verify exact source checkout")

    assert f"ref: {source_expr}" in checkout
    assert "clean: true" in checkout
    assert f"EXPECTED_SHA: {source_expr}" in verify
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in verify
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in verify

    assert text.index("name: Verify exact source checkout") < text.index(
        "name: Create isolated test environment"
    )
    assert text.index("name: Verify exact source checkout") < text.index(
        "name: Test"
    )


MANDATORY_VALIDATION_WORKFLOWS = (
    ".github/workflows/arena-runner-config-integrity-validation.yml",
    ".github/workflows/arena-v2-evaluation-validation.yml",
    ".github/workflows/arena-v2-integration-validate.yml",
    ".github/workflows/arena-v2-metric-bounds-validation.yml",
    ".github/workflows/calibration-gate-contract-validation.yml",
    ".github/workflows/calibration-pointer-invariant-validation.yml",
    ".github/workflows/closed-loop-native-numeric-validation.yml",
    ".github/workflows/duel-policy-integrity-validation.yml",
    ".github/workflows/elite-gate-preflight-integrity-validation.yml",
    ".github/workflows/evaluation-green-integration-validation.yml",
    ".github/workflows/multisource-suite-integrity-validation.yml",
    ".github/workflows/promotion-evidence-validation.yml",
    ".github/workflows/promotion-policy-integrity-validation.yml",
    ".github/workflows/replay-scenario-state-integrity-validation.yml",
    ".github/workflows/runtime-model-integrity-validation.yml",
    ".github/workflows/scenario-source-integrity-validation.yml",
)


def _first_checkout_block(text: str) -> str:
    checkout = text.index("uses: actions/checkout@v4")
    start = text.rfind("- name:", 0, checkout)
    if start == -1:
        start = checkout
    end = text.find("\n      - name:", checkout)
    if end == -1:
        end = len(text)
    return text[start:end]


def test_mandatory_validation_workflows_keep_runner_permission_and_source_contracts() -> None:
    for workflow in MANDATORY_VALIDATION_WORKFLOWS:
        text = Path(workflow).read_text(encoding="utf-8")
        _assert_read_only_self_hosted(text)

        checkout = _first_checkout_block(text)
        assert "ref:" in checkout
        assert any(
            marker in checkout
            for marker in (
                "github.sha",
                "github.event.pull_request.head.sha",
                "inputs.ref",
            )
        )
        assert "github.ref" not in checkout
        assert "clean: true" in checkout


def test_post_gate_resync_keeps_fresh_exact_head_revalidation_routes() -> None:
    calibration = CALIBRATION.read_text(encoding="utf-8")
    multisource = MULTISOURCE.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")

    assert "if: contains(github.event.head_commit.message, '[arena-v2-calibration]')" in calibration
    assert f"- {CANONICAL_BRANCH}" in calibration

    assert "workflow_dispatch:" in multisource
    assert "ref: ${{ github.sha }}" in multisource

    assert "workflow_dispatch:" in ci
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in ci


def test_canonical_evaluation_workflows_have_bounded_runner_timeouts() -> None:
    workflows = (
        *MANDATORY_VALIDATION_WORKFLOWS,
        str(CALIBRATION),
        str(MULTISOURCE),
        str(CI),
    )
    for workflow in workflows:
        text = Path(workflow).read_text(encoding="utf-8")
        timeouts = [
            int(line.split(":", 1)[1].strip())
            for line in text.splitlines()
            if line.strip().startswith("timeout-minutes:")
        ]
        assert timeouts, workflow
        assert all(1 <= timeout <= 360 for timeout in timeouts), (
            workflow,
            timeouts,
        )


def test_canonical_evaluation_workflows_do_not_request_write_permissions() -> None:
    workflows = (
        *MANDATORY_VALIDATION_WORKFLOWS,
        str(CALIBRATION),
        str(MULTISOURCE),
        str(CI),
    )
    for workflow in workflows:
        text = Path(workflow).read_text(encoding="utf-8")
        permissions_start = text.index("permissions:")
        jobs_start = text.index("\njobs:", permissions_start)
        permissions = text[permissions_start:jobs_start]

        assert "contents: read" in permissions, workflow
        assert "write" not in permissions, (workflow, permissions)
