from __future__ import annotations

import ast
from pathlib import Path


EVALUATION_ROOT = Path("src/haxlab/evaluation")


def test_evaluation_product_code_does_not_use_python_assertions() -> None:
    """Safety gates must survive execution with Python optimization enabled."""

    violations: list[str] = []
    for path in sorted(EVALUATION_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                violations.append(f"{path}:{node.lineno}")

    assert not violations, (
        "evaluation product code must use explicit fail-closed checks; "
        "Python -O strips assert statements: "
        + ", ".join(violations)
    )
