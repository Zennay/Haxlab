import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "evaluation_workflow_provenance_inventory.py"
)
SPEC = importlib.util.spec_from_file_location(
    "haxlab_evaluation_workflow_provenance_inventory",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DEFAULT_WORKFLOWS = MODULE.DEFAULT_WORKFLOWS
audit_workflow_text = MODULE.audit_workflow_text
build_inventory = MODULE.build_inventory
main = MODULE.main

PINNED_CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"


def _clean_workflow() -> str:
    return f"""name: provenance fixture

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  validate:
    runs-on: [self-hosted, haxlab]
    timeout-minutes: 20
    steps:
      - name: Checkout exact source
        uses: {PINNED_CHECKOUT}
        with:
          ref: github.sha
          clean: true
          persist-credentials: false
      - name: Verify exact source
        env:
          EXPECTED_SHA: github.sha
        run: |
          set -euo pipefail
          test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
"""


def test_clean_pinned_workflow_has_no_findings() -> None:
    report = audit_workflow_text(".github/workflows/example.yml", _clean_workflow())

    assert report["findings"] == []
    assert report["mutable_actions"] == []
    assert report["checkout_credentials_disabled"] is True
    assert report["checkout_clean"] is True
    assert report["checkout_ref_kinds"] == ["event_source"]
    assert report["checkout_refs_bound"] is True
    assert report["has_exact_head_guard"] is True
    assert report["top_level_contents_read_only"] is True
    assert report["runner_specs"] == ["[self-hosted, haxlab]"]
    assert report["self_hosted_haxlab_only"] is True
    assert report["timeout_minutes"] == [20]
    assert report["bounded_timeouts"] is True
    assert report["continue_on_error_enabled"] is False




def test_dispatch_input_ref_requires_exact_sha_validation() -> None:
    text = _clean_workflow().replace("ref: github.sha", "ref: inputs.ref")
    report = audit_workflow_text(".github/workflows/input-ref.yml", text)

    assert report["checkout_ref_kinds"] == ["mutable_input"]
    assert report["checkout_refs_bound"] is False
    assert "checkout_ref_unbound" in report["findings"]


def test_exact_sha_validated_dispatch_input_is_bound() -> None:
    text = _clean_workflow().replace(
        "      - name: Checkout exact source",
        """      - name: Validate requested ref
        env:
          TARGET_SHA: inputs.ref
        run: |
          set -euo pipefail
          if ! [[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
            exit 1
          fi
      - name: Checkout exact source""",
    ).replace("ref: github.sha", "ref: inputs.ref")
    report = audit_workflow_text(".github/workflows/validated-input-ref.yml", text)

    assert report["checkout_ref_kinds"] == ["validated_input"]
    assert report["checkout_refs_bound"] is True
    assert "checkout_ref_unbound" not in report["findings"]


def test_unrelated_sha_regex_does_not_validate_dispatch_input() -> None:
    text = _clean_workflow().replace(
        "      - name: Checkout exact source",
        """      - name: Pretend validation
        env:
          TARGET_SHA: inputs.ref
          OTHER_VALUE: deadbeef
        run: |
          set -euo pipefail
          if ! [[ "$OTHER_VALUE" =~ ^[0-9a-f]{40}$ ]]; then
            exit 1
          fi
      - name: Checkout exact source""",
    ).replace("ref: github.sha", "ref: inputs.ref")
    report = audit_workflow_text(".github/workflows/unrelated-regex.yml", text)

    assert report["checkout_ref_kinds"] == ["mutable_input"]
    assert report["checkout_refs_bound"] is False


def test_nonfailing_sha_regex_does_not_validate_dispatch_input() -> None:
    text = _clean_workflow().replace(
        "      - name: Checkout exact source",
        """      - name: Nonfailing validation
        env:
          TARGET_SHA: inputs.ref
        run: |
          if [[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
            echo valid
          fi
      - name: Checkout exact source""",
    ).replace("ref: github.sha", "ref: inputs.ref")
    report = audit_workflow_text(".github/workflows/nonfailing-input.yml", text)

    assert report["checkout_ref_kinds"] == ["mutable_input"]
    assert report["checkout_refs_bound"] is False


def test_hybrid_dispatch_ref_requires_input_validation() -> None:
    text = _clean_workflow().replace(
        "ref: github.sha",
        "ref: github.event_name == 'workflow_dispatch' && inputs.ref || github.sha",
    )
    report = audit_workflow_text(".github/workflows/hybrid-ref.yml", text)

    assert report["checkout_ref_kinds"] == ["mutable_input"]
    assert report["checkout_refs_bound"] is False


def test_validated_hybrid_dispatch_ref_is_bound() -> None:
    text = _clean_workflow().replace(
        "      - name: Checkout exact source",
        """      - name: Validate requested ref
        env:
          TARGET_SHA: inputs.ref
        run: |
          set -euo pipefail
          if ! [[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
            exit 1
          fi
      - name: Checkout exact source""",
    ).replace(
        "ref: github.sha",
        "ref: github.event_name == 'workflow_dispatch' && inputs.ref || github.sha",
    )
    report = audit_workflow_text(".github/workflows/validated-hybrid.yml", text)

    assert report["checkout_ref_kinds"] == ["validated_input_or_event"]
    assert report["checkout_refs_bound"] is True


def test_immutable_secondary_checkout_is_provenance_bound() -> None:
    text = _clean_workflow().replace(
        "      - name: Verify exact source",
        """      - name: Checkout frozen model source
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          ref: 4dd926b1eef89509eb271088378f129aa7e72c57
          path: .frozen-source
          clean: true
          persist-credentials: false
      - name: Verify exact source""",
    )
    report = audit_workflow_text(".github/workflows/multi-checkout.yml", text)

    assert report["checkout_ref_kinds"] == [
        "event_source",
        "immutable_commit",
    ]
    assert report["checkout_refs_bound"] is True
    assert "checkout_ref_unbound" not in report["findings"]


def test_mutable_secondary_checkout_ref_is_reported() -> None:
    text = _clean_workflow().replace(
        "      - name: Verify exact source",
        """      - name: Checkout mutable model source
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          ref: model-candidate
          path: .candidate
          clean: true
          persist-credentials: false
      - name: Verify exact source""",
    )
    report = audit_workflow_text(".github/workflows/mutable-ref.yml", text)

    assert report["checkout_ref_kinds"] == [
        "event_source",
        "mutable_or_unbound",
    ]
    assert report["checkout_refs_bound"] is False
    assert "checkout_ref_unbound" in report["findings"]


def test_non_enforcing_equality_is_not_an_exact_head_guard() -> None:
    text = _clean_workflow().replace(
        "          set -euo pipefail\n",
        "",
        1,
    ).replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"\n'
        '          echo continued',
    )
    report = audit_workflow_text(".github/workflows/non-enforcing.yml", text)

    assert report["has_exact_head_guard"] is False
    assert "missing_exact_head_guard" in report["findings"]


def test_recording_head_without_comparison_is_not_an_exact_head_guard() -> None:
    text = _clean_workflow().replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'echo "VALIDATION_HEAD=$(git rev-parse HEAD)"',
    )
    report = audit_workflow_text(".github/workflows/record-only.yml", text)

    assert report["has_exact_head_guard"] is False
    assert "missing_exact_head_guard" in report["findings"]


def test_ci_style_actual_expected_comparison_is_an_exact_head_guard() -> None:
    text = _clean_workflow().replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'ACTUAL_SHA="$(git rev-parse HEAD)"\n'
        '          if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then\n'
        '            exit 1\n'
        '          fi',
    )
    report = audit_workflow_text(".github/workflows/ci-style.yml", text)

    assert report["has_exact_head_guard"] is True
    assert "missing_exact_head_guard" not in report["findings"]


def test_inverted_direct_head_test_is_not_a_guard() -> None:
    text = _clean_workflow().replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'test "$(git rev-parse HEAD)" != "$EXPECTED_SHA"',
    )
    report = audit_workflow_text(".github/workflows/inverted.yml", text)

    assert report["has_exact_head_guard"] is False
    assert "missing_exact_head_guard" in report["findings"]


def test_mismatch_branch_without_nonzero_exit_is_not_a_guard() -> None:
    text = _clean_workflow().replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'ACTUAL_SHA="$(git rev-parse HEAD)"\n'
        '          if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then\n'
        '            echo mismatch\n'
        '          fi',
    )
    report = audit_workflow_text(".github/workflows/nonfailing-mismatch.yml", text)

    assert report["has_exact_head_guard"] is False
    assert "missing_exact_head_guard" in report["findings"]


def test_mutable_checkout_and_missing_guard_are_reported() -> None:
    text = """name: mutable fixture

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  validate:
    runs-on: [self-hosted, haxlab]
    timeout-minutes: 20
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          ref: github.sha
          clean: true
"""
    report = audit_workflow_text(".github/workflows/mutable.yml", text)

    assert report["mutable_actions"] == ["actions/checkout@v4"]
    assert report["findings"] == [
        "mutable_action_refs",
        "checkout_persists_credentials",
        "missing_exact_head_guard",
    ]


def test_write_permissions_are_visible_without_guessing_intent() -> None:
    text = _clean_workflow().replace(
        "  contents: read\n",
        "  contents: read\n  issues: write\n",
    )
    report = audit_workflow_text(".github/workflows/receipt.yml", text)

    assert report["write_permissions"] == ["issues: write"]
    assert report["findings"] == ["write_permissions_present"]


def test_unexpected_runner_is_reported() -> None:
    text = _clean_workflow().replace(
        "runs-on: [self-hosted, haxlab]",
        "runs-on: ubuntu-latest",
    )
    report = audit_workflow_text(".github/workflows/hosted.yml", text)

    assert report["self_hosted_haxlab_only"] is False
    assert "unexpected_runner" in report["findings"]


def test_missing_timeout_is_reported() -> None:
    text = _clean_workflow().replace("    timeout-minutes: 20\n", "")
    report = audit_workflow_text(".github/workflows/unbounded.yml", text)

    assert report["bounded_timeouts"] is False
    assert "unbounded_or_invalid_timeout" in report["findings"]


def test_continue_on_error_true_is_reported() -> None:
    text = _clean_workflow().replace(
        "      - name: Verify exact source",
        "      - name: Optional failure\n"
        "        continue-on-error: true\n"
        "        run: false\n"
        "      - name: Verify exact source",
    )
    report = audit_workflow_text(".github/workflows/masked.yml", text)

    assert report["continue_on_error_enabled"] is True
    assert "continue_on_error_enabled" in report["findings"]


def test_job_level_write_permission_is_reported() -> None:
    text = _clean_workflow().replace(
        "    runs-on: [self-hosted, haxlab]",
        "    permissions:\n"
        "      contents: read\n"
        "      checks: write\n"
        "    runs-on: [self-hosted, haxlab]",
    )
    report = audit_workflow_text(".github/workflows/job-write.yml", text)

    assert report["write_permissions"] == ["checks: write"]
    assert "write_permissions_present" in report["findings"]


def test_permission_like_shell_text_is_not_reported() -> None:
    text = _clean_workflow().replace(
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'echo "issues: write"\n'
        '          test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
    )
    report = audit_workflow_text(".github/workflows/script-text.yml", text)

    assert report["write_permissions"] == []
    assert "write_permissions_present" not in report["findings"]


def test_inventory_is_sorted_deduplicated_and_deterministic(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    for name in ("b.yml", "a.yml"):
        (workflow_dir / name).write_text(_clean_workflow(), encoding="utf-8")

    first = build_inventory(
        tmp_path,
        (
            ".github/workflows/b.yml",
            ".github/workflows/a.yml",
            ".github/workflows/b.yml",
        ),
    )
    second = build_inventory(
        tmp_path,
        (
            ".github/workflows/a.yml",
            ".github/workflows/b.yml",
        ),
    )

    assert first == second
    assert [item["path"] for item in first["workflows"]] == [
        ".github/workflows/a.yml",
        ".github/workflows/b.yml",
    ]
    assert first["finding_count"] == 0
    assert first["workflows_with_findings"] == 0
    assert first["finding_totals"] == {}


def test_inventory_summarizes_findings_by_category(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "clean.yml").write_text(_clean_workflow(), encoding="utf-8")
    (workflow_dir / "mutable.yml").write_text(
        _clean_workflow().replace(PINNED_CHECKOUT, "actions/checkout@v4"),
        encoding="utf-8",
    )

    report = build_inventory(
        tmp_path,
        (
            ".github/workflows/clean.yml",
            ".github/workflows/mutable.yml",
        ),
    )

    assert report["workflows_with_findings"] == 1
    assert report["finding_count"] == 1
    assert report["finding_totals"] == {"mutable_action_refs": 1}


def test_inventory_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        build_inventory(tmp_path, ("../outside.yml",))


def test_strict_mode_returns_two_for_findings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    target = workflow_dir / "mutable.yml"
    target.write_text(
        _clean_workflow().replace(PINNED_CHECKOUT, "actions/checkout@v4"),
        encoding="utf-8",
    )

    rc = main(
        [
            "--repo-root",
            str(tmp_path),
            "--workflow",
            ".github/workflows/mutable.yml",
            "--strict",
        ]
    )

    assert rc == 2
    assert '"mutable_action_refs"' in capsys.readouterr().out


def test_default_canonical_workflow_set_is_unique_and_present() -> None:
    assert len(DEFAULT_WORKFLOWS) == len(set(DEFAULT_WORKFLOWS))
    report = build_inventory(Path("."))

    assert report["workflow_count"] == len(DEFAULT_WORKFLOWS)
    assert report["missing_workflows"] == []
    assert all(item["self_hosted_haxlab_only"] for item in report["workflows"])
    assert all(item["bounded_timeouts"] for item in report["workflows"])
    assert all(not item["continue_on_error_enabled"] for item in report["workflows"])

    by_path = {item["path"]: item for item in report["workflows"]}
    assert by_path[
        ".github/workflows/closed-loop-arena-v2-calibration.yml"
    ]["checkout_ref_kinds"] == ["event_source", "immutable_commit"]
    assert by_path[
        ".github/workflows/arena-v2-integration-validate.yml"
    ]["checkout_ref_kinds"] == ["validated_input_or_event"]
