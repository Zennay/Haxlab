from __future__ import annotations

import re
from pathlib import Path


MANDATORY_EVALUATION_WORKFLOWS = (
    Path(".github/workflows/closed-loop-arena-v2-calibration.yml"),
    Path(".github/workflows/multisource-suite-v2.yml"),
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/arena-v2-integration-validate.yml"),
)
REPOSITORY_ASSET_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:tests|tools|configs|src)/[A-Za-z0-9_./-]+\.(?:py|js|json|toml|npz))(?![A-Za-z0-9_.-])"
)


def test_mandatory_evaluation_workflows_only_reference_existing_repository_assets() -> None:
    missing: list[str] = []

    for workflow in MANDATORY_EVALUATION_WORKFLOWS:
        assert workflow.is_file(), f"missing mandatory evaluation workflow: {workflow}"
        workflow_text = workflow.read_text(encoding="utf-8")
        referenced_assets = sorted(set(REPOSITORY_ASSET_REFERENCE.findall(workflow_text)))

        assert referenced_assets, (
            f"{workflow} must reference at least one repository evaluation asset"
        )

        missing.extend(
            f"{workflow}:{path}"
            for path in referenced_assets
            if not Path(path).is_file()
        )

    assert not missing, (
        "Mandatory evaluation workflows contain stale repository references: "
        + ", ".join(missing)
    )
