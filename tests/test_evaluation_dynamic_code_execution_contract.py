from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BANNED_RUNTIME_CODE_CALLS = {
    "compile",
    "eval",
    "exec",
    "builtins.compile",
    "builtins.eval",
    "builtins.exec",
}

BANNED_RUNTIME_CODE_MODULES = {
    "code",
    "codeop",
}


class DynamicCodeExecutionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
            if alias.name in BANNED_RUNTIME_CODE_MODULES:
                self.violations.append(
                    f"line {node.lineno}: runtime code helper import is forbidden: {alias.name!r}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            self.generic_visit(node)
            return

        if node.module in BANNED_RUNTIME_CODE_MODULES:
            self.violations.append(
                f"line {node.lineno}: runtime code helper import is forbidden: {node.module!r}"
            )

        for alias in node.names:
            if alias.name == "*":
                if node.module == "builtins":
                    self.violations.append(
                        f"line {node.lineno}: wildcard builtins import can expose runtime code execution"
                    )
                continue
            qualified = f"{node.module}.{alias.name}"
            self.aliases[alias.asname or alias.name] = qualified

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        resolved = self._qualified_name(node.value)
        if resolved is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = resolved
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            resolved = self._qualified_name(node.value)
            if resolved is not None:
                self.aliases[node.target.id] = resolved
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.target, ast.Name):
            resolved = self._qualified_name(node.value)
            if resolved is not None:
                self.aliases[node.target.id] = resolved
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target in BANNED_RUNTIME_CODE_CALLS:
            self.violations.append(
                f"line {node.lineno}: runtime code execution/compilation is forbidden: {target!r}"
            )
        self.generic_visit(node)

    def _qualified_name(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None

        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)

        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            return None if parent is None else f"{parent}.{node.attr}"

        if isinstance(node, ast.Call):
            accessor = self._qualified_name(node.func)
            if (
                accessor in {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                owner = self._qualified_name(node.args[0])
                if owner:
                    return f"{owner}.{node.args[1].value}"

        if isinstance(node, ast.Subscript):
            key = self._constant_string(node.slice)
            owner = self._qualified_name(node.value)
            if key in {"compile", "eval", "exec"}:
                if owner in {"__builtins__", "builtins.__dict__"}:
                    return f"builtins.{key}"

        return None

    @staticmethod
    def _constant_string(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = DynamicCodeExecutionVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def test_evaluation_package_has_no_runtime_dynamic_code_execution() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("eval('1 + 1')\n", "eval"),
        ("exec('value = 1')\n", "exec"),
        ("compile('1 + 1', '<memory>', 'eval')\n", "compile"),
        ("import builtins\nbuiltins.eval('1 + 1')\n", "builtins.eval"),
        ("import builtins as b\nb.exec('value = 1')\n", "builtins.exec"),
        ("from builtins import compile as make_code\nmake_code('1', '<m>', 'eval')\n", "builtins.compile"),
        ("runner = eval\nrunner('1 + 1')\n", "eval"),
        ("runner: object = exec\nrunner('value = 1')\n", "exec"),
        ("(runner := compile)('1', '<m>', 'eval')\n", "compile"),
        ("import builtins\ngetattr(builtins, 'eval')('1 + 1')\n", "builtins.eval"),
        ("__builtins__['exec']('value = 1')\n", "builtins.exec"),
        ("import builtins\nbuiltins.__dict__['compile']('1', '<m>', 'eval')\n", "builtins.compile"),
        ("import code\n", "code"),
        ("from codeop import compile_command\n", "codeop"),
        ("from builtins import *\n", "wildcard builtins"),
    ],
)
def test_detector_rejects_runtime_code_execution(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(source))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import ast\nast.parse('value = 1')\n",
        "import builtins\nvalue = builtins.len([1, 2, 3])\n",
        "class Evaluator:\n    def eval(self, value):\n        return value\nEvaluator().eval(1)\n",
        "def compile_report(value):\n    return str(value)\ncompile_report(1)\n",
        "from haxlab.evaluation.models import PromotionDecision\n",
    ],
)
def test_detector_allows_static_reviewable_evaluation_code(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
