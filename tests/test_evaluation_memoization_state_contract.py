from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BANNED_MEMOIZATION_HELPERS = {
    "functools.cache",
    "functools.cached_property",
    "functools.lru_cache",
}


class MemoizationStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    if node.module == "functools":
                        self.violations.append(
                            f"line {node.lineno}: wildcard functools import can "
                            "expose stateful memoization helpers"
                        )
                    continue
                self.aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_assignment_alias(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._record_assignment_alias([node.target], node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._record_assignment_alias([node.target], node.value)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_decorators(node.decorator_list)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_decorators(node.decorator_list)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_decorators(node.decorator_list)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target in BANNED_MEMOIZATION_HELPERS:
            self.violations.append(
                f"line {node.lineno}: stateful memoization is forbidden: {target}"
            )
        self.generic_visit(node)

    def _record_assignment_alias(
        self,
        targets: list[ast.expr],
        value: ast.expr,
    ) -> None:
        qualified = self._qualified_name(value)
        if qualified is None:
            return
        for target in targets:
            if isinstance(target, ast.Name):
                self.aliases[target.id] = qualified

    def _check_decorators(self, decorators: list[ast.expr]) -> None:
        for decorator in decorators:
            target = self._qualified_name(
                decorator.func if isinstance(decorator, ast.Call) else decorator
            )
            if target in BANNED_MEMOIZATION_HELPERS:
                self.violations.append(
                    f"line {decorator.lineno}: stateful memoization decorator "
                    f"is forbidden: {target}"
                )

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
                and len(node.args) in {2, 3}
                and not node.keywords
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                owner = self._qualified_name(node.args[0])
                if owner is not None:
                    return f"{owner}.{node.args[1].value}"

            if (
                accessor in {"vars", "builtins.vars"}
                and len(node.args) == 1
                and not node.keywords
            ):
                owner = self._qualified_name(node.args[0])
                if owner is not None:
                    return f"{owner}.__dict__"

            if (
                accessor == "functools.__dict__.get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                return f"functools.{node.args[0].value}"

        if isinstance(node, ast.Subscript):
            owner = self._qualified_name(node.value)
            if (
                owner == "functools.__dict__"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                return f"functools.{node.slice.value}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = MemoizationStateVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def test_evaluation_package_has_no_stateful_memoization() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        ):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import functools\n@functools.cache\ndef f(x):\n    return x\n", "functools.cache"),
        ("from functools import cache\n@cache\ndef f(x):\n    return x\n", "functools.cache"),
        ("from functools import lru_cache\n@lru_cache()\ndef f(x):\n    return x\n", "functools.lru_cache"),
        ("import functools as ft\n@ft.lru_cache(maxsize=8)\ndef f(x):\n    return x\n", "functools.lru_cache"),
        ("from functools import cached_property\nclass C:\n    @cached_property\n    def value(self):\n        return 1\n", "functools.cached_property"),
        ("import functools\nwrapped = functools.cache(lambda x: x)\n", "functools.cache"),
        ("import functools\nmemo = functools.lru_cache\nwrapped = memo(maxsize=4)(lambda x: x)\n", "functools.lru_cache"),
        ("import functools\nmemo = getattr(functools, 'cache')\nwrapped = memo(lambda x: x)\n", "functools.cache"),
        ("import functools as ft\nmemo = getattr(ft, 'lru_cache')\n@memo(maxsize=None)\ndef f(x):\n    return x\n", "functools.lru_cache"),
        ("from functools import cached_property as cp\nwrapper = cp\nclass C:\n    @wrapper\n    def value(self):\n        return 1\n", "functools.cached_property"),
        ("from functools import cache\n@cache\nclass C:\n    pass\n", "functools.cache"),
        ("import functools\nmemo = getattr(functools, 'cache', None)\nwrapped = memo(lambda x: x)\n", "functools.cache"),
        ("import functools\nmemo = functools.__dict__['lru_cache']\n@memo(maxsize=8)\ndef f(x):\n    return x\n", "functools.lru_cache"),
        ("import functools\nmemo = vars(functools)['cache']\nwrapped = memo(lambda x: x)\n", "functools.cache"),
        ("import functools\nmemo = functools.__dict__.get('cached_property')\nclass C:\n    @memo\n    def value(self):\n        return 1\n", "functools.cached_property"),
        ("import functools\nmemo = vars(functools).get('lru_cache')\n@memo()\ndef f(x):\n    return x\n", "functools.lru_cache"),
        ("from functools import *\n", "wildcard functools"),
    ],
)
def test_detector_rejects_stateful_memoization(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import functools\nfunctools.reduce(lambda a, b: a + b, [1, 2], 0)\n",
        "from functools import partial\nhelper = partial(pow, 2)\n",
        "from functools import wraps\ndef deco(fn):\n    @wraps(fn)\n    def wrapped(*args, **kwargs):\n        return fn(*args, **kwargs)\n    return wrapped\n",
        "def f(value):\n    local_cache = {}\n    return local_cache.setdefault(value, value)\n",
        "def f(values):\n    return tuple(sorted(values))\n",
    ],
)
def test_detector_allows_stateless_helpers_and_per_call_locals(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
