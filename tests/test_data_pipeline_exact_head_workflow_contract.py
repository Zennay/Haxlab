from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_ROOT = Path(".github/workflows")
GRANDFATHERED_PR_MERGE_REF_WORKFLOWS = frozenset(
    {
        ".github/workflows/data-pipeline-autonomy-status-proof.yml",
    }
)
_PULL_REQUEST_TRIGGER = re.compile(r"(?m)^\s*pull_request\s*:")
_EXACT_HEAD_WORDING = re.compile(r"exact(?:-| )head", re.IGNORECASE)


def _claims_exact_head(text: str) -> bool:
    return _EXACT_HEAD_WORDING.search(text) is not None


def _has_pull_request_trigger(text: str) -> bool:
    return _PULL_REQUEST_TRIGGER.search(text) is not None


def _binds_explicit_pull_request_head(text: str) -> bool:
    return "github.event.pull_request.head.sha" in text


def _is_pr_exact_head_merge_ref_risk(text: str) -> bool:
    return (
        _claims_exact_head(text)
        and _has_pull_request_trigger(text)
        and "actions/checkout@" in text
        and not _binds_explicit_pull_request_head(text)
    )


def _workflow_paths() -> tuple[Path, ...]:
    paths = set(WORKFLOW_ROOT.glob("data-pipeline-*.yml"))
    paths.update(WORKFLOW_ROOT.glob("data-pipeline-*.yaml"))
    return tuple(sorted(paths))


def test_no_new_pr_exact_head_workflow_relies_on_merge_ref() -> None:
    offenders = {
        path.as_posix()
        for path in _workflow_paths()
        if _is_pr_exact_head_merge_ref_risk(path.read_text(encoding="utf-8"))
    }

    unexpected = offenders - GRANDFATHERED_PR_MERGE_REF_WORKFLOWS

    assert not unexpected, (
        "PR-triggered exact-head workflows must bind "
        "github.event.pull_request.head.sha explicitly; "
        f"new offenders: {sorted(unexpected)}"
    )


def test_detector_rejects_pull_request_merge_ref_semantics() -> None:
    workflow = """
name: Example exact-head proof
on:
  pull_request:
jobs:
  proof:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.sha }}
"""

    assert _is_pr_exact_head_merge_ref_risk(workflow) is True


def test_detector_accepts_explicit_pull_request_head_binding() -> None:
    workflow = """
name: Example exact-head proof
on:
  pull_request:
jobs:
  proof:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
"""

    assert _is_pr_exact_head_merge_ref_risk(workflow) is False


def test_detector_does_not_flag_dispatch_only_github_sha() -> None:
    workflow = """
name: Example exact-head proof
on:
  workflow_dispatch:
jobs:
  proof:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.sha }}
"""

    assert _is_pr_exact_head_merge_ref_risk(workflow) is False
