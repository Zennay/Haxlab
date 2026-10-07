from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/arena-v2-integration-validate.yml")
TEST_REFERENCE = re.compile(r"(?<![A-Za-z0-9_./-])(tests/[A-Za-z0-9_./-]+\.(?:py|js))(?![A-Za-z0-9_.-])")


def test_arena_integration_workflow_only_references_existing_tests() -> None:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    referenced_tests = sorted(set(TEST_REFERENCE.findall(workflow_text)))

    assert referenced_tests, "Arena integration workflow must reference at least one test file"

    missing = [path for path in referenced_tests if not Path(path).is_file()]
    assert not missing, (
        "Arena integration workflow contains stale test references: "
        + ", ".join(missing)
    )
