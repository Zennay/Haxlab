from __future__ import annotations

import ast
import re
from pathlib import Path


CALIBRATION_WORKFLOW = Path(
    ".github/workflows/closed-loop-arena-v2-calibration.yml"
)
EXPECTED_MODULES = {
    "haxlab.evaluation.calibration_resume",
    "haxlab.evaluation.closed_loop_arena",
}


def _module_path(module: str) -> Path:
    return Path("src").joinpath(*module.split(".")).with_suffix(".py")


def _main_guard_calls_main(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            continue
        return any(
            isinstance(child, ast.Call)
            and (
                (
                    isinstance(child.func, ast.Name)
                    and child.func.id == "main"
                )
                or (
                    isinstance(child.func, ast.Name)
                    and child.func.id == "SystemExit"
                    and child.args
                    and isinstance(child.args[0], ast.Call)
                    and isinstance(child.args[0].func, ast.Name)
                    and child.args[0].func.id == "main"
                )
            )
            for statement in node.body
            for child in ast.walk(statement)
        )
    return False


def test_calibration_workflow_module_clis_are_explicit_and_stable() -> None:
    workflow = CALIBRATION_WORKFLOW.read_text(encoding="utf-8")
    modules = set(
        re.findall(
            r"python\s+-m\s+(haxlab\.evaluation\.[A-Za-z0-9_]+)",
            workflow,
        )
    )

    assert modules == EXPECTED_MODULES


def test_workflow_invoked_evaluation_modules_have_executable_main_guards() -> None:
    for module in EXPECTED_MODULES:
        path = _module_path(module)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        top_level_main = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
            for node in tree.body
        )
        assert top_level_main, f"{module} lost its main() entrypoint"
        assert _main_guard_calls_main(tree), (
            f"{module} is invoked with python -m but no longer executes main()"
        )
