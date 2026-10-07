from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/arena-v2-integration-validate.yml")
REPOSITORY_ASSET_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:tests|tools)/[A-Za-z0-9_./-]+\.(?:py|js))(?![A-Za-z0-9_.-])"
)


def test_arena_integration_workflow_only_references_existing_repository_assets() -> None:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    referenced_assets = sorted(set(REPOSITORY_ASSET_REFERENCE.findall(workflow_text)))

    assert referenced_assets, (
        "Arena integration workflow must reference at least one test or runtime asset"
    )

    missing = [path for path in referenced_assets if not Path(path).is_file()]
    assert not missing, (
        "Arena integration workflow contains stale repository references: "
        + ", ".join(missing)
    )
