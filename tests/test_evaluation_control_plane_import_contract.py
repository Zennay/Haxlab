from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_FIRST_PARTY_PREFIXES = (
    "haxlab.ingestion",
    "haxlab.learning",
    "haxlab.live",
    "haxlab.research",
    "haxlab.runtime",
    "haxlab.training",
)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_FIRST_PARTY_PREFIXES
    )


class EvaluationImportBoundaryVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_forbidden(alias.name):
                self.violations.append(
                    f"line {node.lineno}: evaluation must not import mutable control-plane module {alias.name!r}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level >= 2:
            target = "." * node.level + (node.module or "")
            self.violations.append(
                f"line {node.lineno}: relative import escapes haxlab.evaluation: {target!r}"
            )
        elif node.level == 0 and node.module is not None and _is_forbidden(node.module):
            self.violations.append(
                f"line {node.lineno}: evaluation must not import mutable control-plane module {node.module!r}"
            )
        self.generic_visit(node)


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = EvaluationImportBoundaryVisitor()
    visitor.visit(tree)
    return visitor.violations


def test_evaluation_package_isolated_from_mutable_control_plane() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "import haxlab.runtime.state\n",
        "import haxlab.live.inference as live\n",
        "from haxlab.learning.selector import select\n",
        "from haxlab.research.generation_loop import run\n",
        "from haxlab.ingestion.pipeline import ingest\n",
        "from haxlab.training import trainer\n",
        "from ..runtime import state\n",
        "from ...haxlab import runtime\n",
    ],
)
def test_detector_rejects_control_plane_dependencies(source: str) -> None:
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "from .models import PromotionDecision\n",
        "from haxlab.evaluation.models import PromotionDecision\n",
        "from haxlab.analysis.roles import ROLES_4V4\n",
        "from haxlab.hashing import sha256_file\n",
        "import json\n",
        "from pathlib import Path\n",
    ],
)
def test_detector_allows_evaluation_and_read_only_data_dependencies(source: str) -> None:
    assert scan_source(source) == []
