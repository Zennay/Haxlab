from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"
MUTABLE_CONSTRUCTORS = {
    "bytearray",
    "builtins.bytearray",
    "builtins.dict",
    "builtins.list",
    "builtins.set",
    "collections.OrderedDict",
    "collections.defaultdict",
    "collections.deque",
    "dict",
    "dict.fromkeys",
    "list",
    "set",
}


class MutableDefaultVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def scan_module(self, tree: ast.Module) -> None:
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                self._record_import(statement)
            elif isinstance(statement, ast.ImportFrom):
                self._record_import_from(statement)
            elif isinstance(statement, ast.Assign):
                self._record_assignment_alias(statement.targets, statement.value)
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                self._record_assignment_alias([statement.target], statement.value)
        self.visit(tree)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_arguments(node.args, node.lineno, node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_arguments(node.args, node.lineno, node.name)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._check_arguments(node.args, node.lineno, "<lambda>")
        self.generic_visit(node)

    def _check_arguments(
        self,
        arguments: ast.arguments,
        lineno: int,
        name: str,
    ) -> None:
        defaults: list[ast.expr] = list(arguments.defaults)
        defaults.extend(
            default
            for default in arguments.kw_defaults
            if default is not None
        )
        for default in defaults:
            if self._is_mutable_default(default):
                self.violations.append(
                    f"line {lineno}: mutable default argument is forbidden in {name}"
                )

    def _record_import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name

    def _record_import_from(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            self.aliases[local] = f"{node.module}.{alias.name}"

    def _record_assignment_alias(
        self,
        targets: list[ast.expr],
        value: ast.expr,
    ) -> None:
        qualified = self._qualified_name(value)
        if qualified not in MUTABLE_CONSTRUCTORS:
            return
        for target in targets:
            if isinstance(target, ast.Name):
                self.aliases[target.id] = qualified

    def _is_mutable_default(self, node: ast.expr) -> bool:
        if isinstance(
            node,
            (
                ast.List,
                ast.Dict,
                ast.Set,
                ast.ListComp,
                ast.DictComp,
                ast.SetComp,
            ),
        ):
            return True
        if isinstance(node, ast.Tuple):
            return any(self._is_mutable_default(element) for element in node.elts)
        if isinstance(node, ast.Call):
            target = self._qualified_name(node.func)
            return target in MUTABLE_CONSTRUCTORS
        return False

    def _qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            if parent is None:
                return None
            return f"{parent}.{node.attr}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = MutableDefaultVisitor()
    visitor.scan_module(tree)
    return visitor.violations


def test_evaluation_package_has_no_mutable_default_arguments() -> None:
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
    "source",
    [
        "def f(cache=[]):\n    return cache\n",
        "def f(*, cache={}):\n    return cache\n",
        "async def f(seen=set()):\n    return seen\n",
        "f = lambda cache=[]: cache\n",
        "def f(value=('stable', [])):\n    return value\n",
        "def f(cache=dict()):\n    return cache\n",
        "def f(cache=dict.fromkeys(('a',))):\n    return cache\n",
        "import collections as c\ndef f(cache=c.defaultdict(list)):\n    return cache\n",
        "from collections import deque as Queue\ndef f(queue=Queue()):\n    return queue\n",
        "from builtins import list as MutableList\ndef f(cache=MutableList()):\n    return cache\n",
        "Factory = dict\ndef f(cache=Factory()):\n    return cache\n",
        "import collections\nQueue = collections.deque\ndef f(queue=Queue()):\n    return queue\n",
    ],
)
def test_detector_rejects_mutable_defaults(source: str) -> None:
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "def f(value=None):\n    return value\n",
        "def f(roles=('gk', 'dm', 'am', 'st')):\n    return roles\n",
        "def f(limit=500):\n    return limit\n",
        "def f(policy=PromotionPolicy()):\n    return policy\n",
        "def f(allowed=frozenset(('a', 'b'))):\n    return allowed\n",
        "def f(factory=tuple):\n    return factory\n",
    ],
)
def test_detector_allows_immutable_or_explicitly_nonmutable_defaults(
    source: str,
) -> None:
    assert scan_source(source) == []
