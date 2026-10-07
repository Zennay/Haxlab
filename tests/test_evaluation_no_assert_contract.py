from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


EVALUATION_ROOT = Path("src/haxlab/evaluation")


def test_evaluation_product_code_is_python_optimization_safe() -> None:
    """Safety gates must not disappear or branch differently under Python -O."""

    violations: list[str] = []
    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
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


def test_all_evaluation_modules_import_under_python_optimization() -> None:
    script = """
import pkgutil
import haxlab.evaluation

modules = sorted(
    module.name
    for module in pkgutil.walk_packages(
        haxlab.evaluation.__path__,
        haxlab.evaluation.__name__ + ".",
    )
)
if not modules:
    raise SystemExit("no evaluation modules discovered")
for name in modules:
    __import__(name)
print(len(modules))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path("src").resolve())
    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0
