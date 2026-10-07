from __future__ import annotations

import ast
from pathlib import Path


EVALUATION_ROOT = Path("src/haxlab/evaluation")


def test_evaluation_product_code_is_python_optimization_safe() -> None:
    """Safety gates must not disappear or branch differently under Python -O."""

    violations: list[str] = []
    for path in sorted(EVALUATION_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                violations.append(f"{path}:{node.lineno}:assert")
            elif isinstance(node, ast.Name) and node.id == "__debug__":
                violations.append(f"{path}:{node.lineno}:__debug__")

    assert not violations, (
        "evaluation product code must use explicit fail-closed checks that "
        "behave identically under Python -O; forbidden optimization-sensitive "
        "constructs: "
        + ", ".join(violations)
    )
