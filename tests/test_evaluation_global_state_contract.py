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
    "list",
    "set",
}


class GlobalStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def scan_module(self, tree: ast.Module) -> None:
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                self._record_import(statement)
            elif isinstance(statement, ast.ImportFrom):
                self._record_import_from(statement)

        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                if value is not None and self._contains_mutable_global(value):
                    self.violations.append(
                        f"line {statement.lineno}: mutable module-global state is forbidden"
                    )

        self.visit(tree)

    def visit_Global(self, node: ast.Global) -> None:
        self.violations.append(
            f"line {node.lineno}: runtime global mutation is forbidden: {', '.join(node.names)}"
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

    def _contains_mutable_global(self, node: ast.expr) -> bool:
        if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
            return True
        if isinstance(node, ast.Tuple):
            return any(self._contains_mutable_global(element) for element in node.elts)
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
    visitor = GlobalStateVisitor()
    visitor.scan_module(tree)
    return visitor.violations


def test_evaluation_package_has_no_mutable_module_global_state() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "CACHE = []\n",
        "CACHE: dict[str, int] = {}\n",
        "SEEN = {item for item in range(3)}\n",
        "PAIR = ('stable', [])\n",
        "CACHE = dict()\n",
        "import collections as c\nCACHE = c.defaultdict(list)\n",
        "from collections import deque as Queue\nQUEUE = Queue()\n",
        "from builtins import list as MutableList\nCACHE = MutableList()\n",
        "VALUE = 0\ndef bump():\n    global VALUE\n    VALUE += 1\n",
    ],
)
def test_detector_rejects_process_lifetime_mutable_state(source: str) -> None:
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "SCHEMA = 'v1'\n",
        "THRESHOLD = 0.5\n",
        "ROLES = ('gk', 'dm', 'am', 'st')\n",
        "ALLOWED = frozenset(('a', 'b'))\n",
        "def build():\n    local = []\n    local.append(1)\n    return local\n",
        "def parse(payload):\n    local = {'payload': payload}\n    return local\n",
    ],
)
def test_detector_allows_immutable_globals_and_local_mutability(source: str) -> None:
    assert scan_source(source) == []
