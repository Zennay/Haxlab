from __future__ import annotations

from types import SimpleNamespace

import pytest

from haxlab.evaluation import resync_guard


def test_evaluation_owned_paths_cover_runtime_tests_docs_and_workflows() -> None:
    protected = (
        "src/haxlab/evaluation/promotion.py",
        "tests/test_closed_loop_arena.py",
        "tests/test_evaluation_resync_guard.py",
        "docs/evaluation.md",
        "docs/closed-loop-arena-v2.md",
        ".github/workflows/ci.yml",
        ".github/workflows/closed-loop-arena-v2-calibration.yml",
        ".github/workflows/multisource-suite-v2.yml",
        "configs/evaluation/multisource-suite-v2.json",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "src/haxlab/analysis/roles.py",
        "src/haxlab/hashing.py",
        "tests/test_closed_loop_arena_v2.js",
        "tools/elite_closed_loop_arena_v2.js",
        "tools/elite_features.js",
        "tools/elite_policy_runtime.js",
        "tools/elite_tactics.js",
        "tools/sandbox_neutral_start.js",
        "tools/sandbox_replay_start.js",
        ".github/workflows/arena-runner-config-integrity-validation.yml",
        ".github/workflows/runtime-model-integrity-validation.yml",
        "tests/test_elite_policy_runtime_integrity.js",
        "tests/test_roles_4v4.py",
        "tests/test_ci_workflow_exact_head_contract.py",
    )

    assert all(resync_guard.is_evaluation_owned_path(path) for path in protected)



def test_canonical_arena_validation_surface_is_fully_guarded() -> None:
    canonical_paths = (
        ".github/workflows/arena-runner-config-integrity-validation.yml",
        ".github/workflows/arena-v2-evaluation-validation.yml",
        ".github/workflows/arena-v2-integration-validate.yml",
        ".github/workflows/arena-v2-metric-bounds-validation.yml",
        ".github/workflows/calibration-gate-contract-validation.yml",
        ".github/workflows/calibration-pointer-invariant-validation.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/closed-loop-arena-v2-calibration.yml",
        ".github/workflows/closed-loop-native-numeric-validation.yml",
        ".github/workflows/duel-policy-integrity-validation.yml",
        ".github/workflows/elite-gate-preflight-integrity-validation.yml",
        ".github/workflows/evaluation-green-integration-validation.yml",
        ".github/workflows/multisource-suite-integrity-validation.yml",
        ".github/workflows/multisource-suite-v2.yml",
        ".github/workflows/promotion-evidence-validation.yml",
        ".github/workflows/promotion-policy-integrity-validation.yml",
        ".github/workflows/replay-scenario-state-integrity-validation.yml",
        ".github/workflows/runtime-model-integrity-validation.yml",
        ".github/workflows/scenario-source-integrity-validation.yml",
        "configs/evaluation/multisource-suite-v2.json",
        "docs/closed-loop-arena-v2.md",
        "src/haxlab/analysis/roles.py",
        "src/haxlab/evaluation/calibration_gate.py",
        "src/haxlab/evaluation/calibration_resume.py",
        "src/haxlab/evaluation/closed_loop_arena.py",
        "src/haxlab/evaluation/duel_gate.py",
        "src/haxlab/evaluation/elite_gate_preflight.py",
        "src/haxlab/evaluation/multisource_duel.py",
        "src/haxlab/evaluation/multisource_suite.py",
        "src/haxlab/evaluation/promotion.py",
        "src/haxlab/evaluation/scenario_source.py",
        "tests/test_arena_v2_integration_workflow_contract.py",
        "tests/test_calibration_gate.py",
        "tests/test_calibration_resume.py",
        "tests/test_calibration_workflow_evidence_contract.py",
        "tests/test_calibration_workflow_runtime_budget.py",
        "tests/test_ci_workflow_exact_head_contract.py",
        "tests/test_closed_loop_arena.py",
        "tests/test_closed_loop_arena_v2.js",
        "tests/test_duel_gate.py",
        "tests/test_elite_gate_preflight.py",
        "tests/test_elite_policy_runtime_integrity.js",
        "tests/test_multisource_duel.py",
        "tests/test_multisource_suite.py",
        "tests/test_promotion.py",
        "tests/test_replay_scenario_state_integrity.js",
        "tests/test_roles_4v4.py",
        "tests/test_scenario_source.py",
        "tools/elite_closed_loop_arena_v2.js",
        "tools/elite_features.js",
        "tools/elite_policy_runtime.js",
        "tools/elite_tactics.js",
        "tools/sandbox_neutral_start.js",
        "tools/sandbox_replay_start.js",
    )

    uncovered = tuple(
        path for path in canonical_paths if not resync_guard.is_evaluation_owned_path(path)
    )

    assert uncovered == ()


def test_unrelated_main_drift_is_safe() -> None:
    report = resync_guard.evaluate_changed_paths(
        (
            "src/haxlab/ingestion/pipeline.py",
            "tests/test_pipeline.py",
            "README.md",
        ),
        base="staging",
        head="main",
    )

    assert report.safe
    assert report.protected_paths == ()


def test_evaluation_drift_fails_closed_and_is_deterministic() -> None:
    report = resync_guard.evaluate_changed_paths(
        (
            "tests/test_promotion.py",
            "src/haxlab/evaluation/promotion.py",
            "tests/test_promotion.py",
            ".github/workflows/ci.yml",
        ),
        base="staging",
        head="main",
    )

    assert not report.safe
    assert report.changed_paths == (
        ".github/workflows/ci.yml",
        "src/haxlab/evaluation/promotion.py",
        "tests/test_promotion.py",
    )
    assert report.protected_paths == report.changed_paths


@pytest.mark.parametrize("path", ("../outside.py", "/tmp/outside.py"))
def test_repository_path_escape_is_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        resync_guard.is_evaluation_owned_path(path)


@pytest.mark.parametrize(
    ("base", "head"),
    (
        ("main", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "refs/heads/main"),
        ("--output=/tmp/guard", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ("A" * 40, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ("deadbeef", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    ),
)
def test_git_diff_requires_exact_immutable_commit_shas(base: str, head: str) -> None:
    with pytest.raises(ValueError, match="exact lowercase 40-character commit SHA"):
        resync_guard.changed_paths_from_git(base, head)


def test_git_diff_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="fatal: bad object deadbeef",
        )

    monkeypatch.setattr(resync_guard.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="git diff failed"):
        resync_guard.changed_paths_from_git("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")


def test_git_diff_uses_three_dot_main_side_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "M\0README.md\0"
                "M\0src/haxlab/evaluation/promotion.py\0"
            ),
            stderr="",
        )

    monkeypatch.setattr(resync_guard.subprocess, "run", fake_run)

    paths = resync_guard.changed_paths_from_git("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert captured["command"] == [
        "git",
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--find-copies",
        "--diff-filter=ACDMRTUXB",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert paths == (
        "README.md",
        "src/haxlab/evaluation/promotion.py",
    )


def test_rename_keeps_old_protected_path_in_guard_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "R100\0"
                "src/haxlab/evaluation/promotion.py\0"
                "src/haxlab/archive/promotion.py\0"
            ),
            stderr="",
        )

    monkeypatch.setattr(resync_guard.subprocess, "run", fake_run)

    report = resync_guard.build_report("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    assert not report.safe
    assert "src/haxlab/evaluation/promotion.py" in report.protected_paths
    assert "src/haxlab/archive/promotion.py" in report.changed_paths


@pytest.mark.parametrize(
    "output",
    (
        "R100\0src/haxlab/evaluation/promotion.py\0",
        "M\0",
        "Q\0README.md\0",
        "M100\0README.md\0",
        "Rabc\0old.py\0new.py\0",
        "R101\0old.py\0new.py\0",
        "C\0old.py\0new.py\0",
    ),
)
def test_malformed_name_status_output_fails_closed(output: str) -> None:
    with pytest.raises(ValueError):
        resync_guard._paths_from_name_status(output)
