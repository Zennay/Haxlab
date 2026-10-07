from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_STATE_FACTORIES = {
    "_thread._local",
    "contextvars.Context",
    "contextvars.ContextVar",
    "contextvars.copy_context",
    "threading.local",
}

TRACKED_MODULES = {
    "_thread",
    "builtins",
    "contextvars",
    "threading",
}


class ModuleScopeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.nodes.append(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.nodes.append(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.nodes.append(node)


def _module_scope_nodes(tree: ast.Module) -> list[ast.AST]:
    collector = ModuleScopeCollector()
    collector.visit(tree)
    return collector.nodes


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _canonical_name(node.args[0], aliases)
            if owner:
                return f"{owner}.{node.args[1].value}"
    return None


def _name_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        result: list[str] = []
        for element in node.elts:
            result.extend(_name_targets(element))
        return result
    return []


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    nodes = _module_scope_nodes(tree)

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in TRACKED_MODULES:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )

    changed = True
    while changed:
        changed = False
        for node in nodes:
            value: ast.AST | None = None
            targets: list[ast.AST] = []

            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]

            if value is None:
                continue

            resolved = _canonical_name(value, aliases)
            if resolved not in FORBIDDEN_STATE_FACTORIES:
                continue

            for target in targets:
                for local in _name_targets(target):
                    if aliases.get(local) != resolved:
                        aliases[local] = resolved
                        changed = True

    return aliases


class ExecutedExpressionVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.factories: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        target = _canonical_name(node.func, self.aliases)
        if target in FORBIDDEN_STATE_FACTORIES:
            self.factories.append((node.lineno, target))
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda body is not executed while its function object is created.
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        # Generator bodies are lazy. Construction alone does not create the
        # context/thread-local object in the expression body.
        return


def _executed_factories(
    value: ast.AST,
    aliases: dict[str, str],
) -> list[tuple[int, str]]:
    visitor = ExecutedExpressionVisitor(aliases)
    visitor.visit(value)
    return visitor.factories


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _module_aliases(tree)
    nodes = _module_scope_nodes(tree)
    findings: list[str] = []

    for node in nodes:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
        elif isinstance(node, ast.NamedExpr):
            value = node.value

        if value is not None:
            for line, factory in _executed_factories(value, aliases):
                findings.append(
                    f"line {line}: module-lifetime context/thread-local state: {factory}"
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            target = _canonical_name(base, aliases)
            if target in {"_thread._local", "threading.local"}:
                findings.append(
                    f"line {node.lineno}: thread-local subclass is forbidden: {target}"
                )

    return sorted(set(findings))


def test_evaluation_package_has_no_context_or_thread_local_persistent_state() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        )
        violations.extend(f"{relative}: {finding}" for finding in findings)

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import contextvars\nCURRENT = contextvars.ContextVar('current')\n", "contextvars.ContextVar"),
        ("from contextvars import ContextVar as CV\nCURRENT = CV('current')\n", "contextvars.ContextVar"),
        ("import contextvars as cv\nCTX = cv.Context()\n", "contextvars.Context"),
        ("import contextvars\nSNAPSHOT = contextvars.copy_context()\n", "contextvars.copy_context"),
        ("import threading\nLOCAL = threading.local()\n", "threading.local"),
        ("from threading import local as Local\nSTATE = Local()\n", "threading.local"),
        ("import _thread\nLOCAL = _thread._local()\n", "_thread._local"),
        (
            "import contextvars\nFactory = contextvars.ContextVar\nCURRENT = Factory('current')\n",
            "contextvars.ContextVar",
        ),
        (
            "import contextvars\nFactory: object = contextvars.ContextVar\nCURRENT = Factory('current')\n",
            "contextvars.ContextVar",
        ),
        (
            "import contextvars\nFactory = getattr(contextvars, 'ContextVar')\nCURRENT = Factory('current')\n",
            "contextvars.ContextVar",
        ),
        (
            "import contextvars\nPAIR = ('stable', contextvars.ContextVar('current'))\n",
            "contextvars.ContextVar",
        ),
        (
            "import threading\nclass ScopedState(threading.local):\n    pass\n",
            "threading.local",
        ),
    ],
)
def test_detector_rejects_module_lifetime_context_local_state(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import contextvars\ndef probe():\n    local = contextvars.ContextVar('temporary')\n    return local.get(None)\n",
        "import threading\ndef probe():\n    local = threading.local()\n    local.value = 1\n    return local.value\n",
        "import contextvars\nFACTORY = lambda: contextvars.ContextVar('later')\n",
        "import contextvars\nFACTORIES = (contextvars.ContextVar for _ in range(1))\n",
        "import threading\nLOCK = threading.Lock()\n",
        "import contextvars\ndef probe():\n    return contextvars.copy_context()\n",
    ],
)
def test_detector_allows_ephemeral_or_non_stateful_usage(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
