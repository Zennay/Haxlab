from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "difference_update",
    "discard",
    "extend",
    "insert",
    "intersection_update",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "symmetric_difference_update",
    "update",
    "__delitem__",
    "__iadd__",
    "__imul__",
    "__ior__",
    "__setitem__",
}

FUNCTION_MUTATORS = {
    "dict.__delitem__",
    "dict.__ior__",
    "dict.__setitem__",
    "dict.clear",
    "dict.pop",
    "dict.popitem",
    "dict.setdefault",
    "dict.update",
    "list.__delitem__",
    "list.__iadd__",
    "list.__imul__",
    "list.__setitem__",
    "list.append",
    "list.clear",
    "list.extend",
    "list.insert",
    "list.pop",
    "list.remove",
    "list.reverse",
    "list.sort",
    "operator.delitem",
    "operator.setitem",
    "set.__ior__",
    "set.add",
    "set.clear",
    "set.difference_update",
    "set.discard",
    "set.intersection_update",
    "set.pop",
    "set.remove",
    "set.symmetric_difference_update",
    "set.update",
}


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    return aliases


def _canonical_name(node: ast.AST | None, imports: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, imports)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _assignment_name_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_assignment_name_targets(element))
        return names
    return []


def _module_function_aliases(tree: ast.Module) -> dict[str, str]:
    aliases = {
        statement.name: statement.name
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
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

            if not isinstance(value, ast.Name):
                continue

            resolved = aliases.get(value.id)
            if resolved is None:
                continue

            for target in targets:
                for local in _assignment_name_targets(target):
                    if aliases.get(local) != resolved:
                        aliases[local] = resolved
                        changed = True

    return aliases


def _function_name(
    node: ast.AST | None,
    function_aliases: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Name):
        return function_aliases.get(node.id)
    return None


def _function_state_root(
    node: ast.AST | None,
    *,
    imports: dict[str, str],
    function_aliases: dict[str, str],
    state_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None

    if isinstance(node, ast.Name):
        return state_aliases.get(node.id)

    if isinstance(node, ast.Attribute):
        owner_function = _function_name(node.value, function_aliases)
        if owner_function is not None:
            return f"{owner_function}.{node.attr}"

        parent_state = _function_state_root(
            node.value,
            imports=imports,
            function_aliases=function_aliases,
            state_aliases=state_aliases,
        )
        if parent_state is not None:
            return f"{parent_state}.{node.attr}"

    if isinstance(node, ast.Subscript):
        return _function_state_root(
            node.value,
            imports=imports,
            function_aliases=function_aliases,
            state_aliases=state_aliases,
        )

    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, imports)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner_function = _function_name(node.args[0], function_aliases)
            if owner_function is not None:
                return f"{owner_function}.{node.args[1].value}"

            parent_state = _function_state_root(
                node.args[0],
                imports=imports,
                function_aliases=function_aliases,
                state_aliases=state_aliases,
            )
            if parent_state is not None:
                return f"{parent_state}.{node.args[1].value}"

    return None


def _state_and_mutator_aliases(
    tree: ast.Module,
    *,
    imports: dict[str, str],
    function_aliases: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    state_aliases: dict[str, str] = {}
    mutator_aliases: dict[str, str] = {}

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
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

            state_root = _function_state_root(
                value,
                imports=imports,
                function_aliases=function_aliases,
                state_aliases=state_aliases,
            )

            bound_mutator: str | None = None
            if isinstance(value, ast.Attribute) and value.attr in MUTATING_METHODS:
                owner_root = _function_state_root(
                    value.value,
                    imports=imports,
                    function_aliases=function_aliases,
                    state_aliases=state_aliases,
                )
                if owner_root is not None:
                    bound_mutator = f"{owner_root}.{value.attr}"

            for target in targets:
                for local in _assignment_name_targets(target):
                    if state_root is not None and state_aliases.get(local) != state_root:
                        state_aliases[local] = state_root
                        changed = True
                    if (
                        bound_mutator is not None
                        and mutator_aliases.get(local) != bound_mutator
                    ):
                        mutator_aliases[local] = bound_mutator
                        changed = True

    return state_aliases, mutator_aliases


def _mutation_target_root(
    node: ast.AST,
    *,
    imports: dict[str, str],
    function_aliases: dict[str, str],
    state_aliases: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Attribute):
        owner_function = _function_name(node.value, function_aliases)
        if owner_function is not None:
            return f"{owner_function}.{node.attr}"

        parent_state = _function_state_root(
            node.value,
            imports=imports,
            function_aliases=function_aliases,
            state_aliases=state_aliases,
        )
        if parent_state is not None:
            return f"{parent_state}.{node.attr}"

    if isinstance(node, ast.Subscript):
        return _function_state_root(
            node.value,
            imports=imports,
            function_aliases=function_aliases,
            state_aliases=state_aliases,
        )

    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    imports = _import_aliases(tree)
    function_aliases = _module_function_aliases(tree)
    state_aliases, mutator_aliases = _state_and_mutator_aliases(
        tree,
        imports=imports,
        function_aliases=function_aliases,
    )
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            targets = []

        for target in targets:
            root = _mutation_target_root(
                target,
                imports=imports,
                function_aliases=function_aliases,
                state_aliases=state_aliases,
            )
            if root is not None:
                findings.append(
                    f"line {node.lineno}: persistent function attribute mutation: {root}"
                )

        if isinstance(node, ast.Delete):
            for target in node.targets:
                root = _mutation_target_root(
                    target,
                    imports=imports,
                    function_aliases=function_aliases,
                    state_aliases=state_aliases,
                )
                if root is not None:
                    findings.append(
                        f"line {node.lineno}: persistent function attribute deletion: {root}"
                    )

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in mutator_aliases:
                findings.append(
                    f"line {node.lineno}: persistent function attribute mutation: "
                    f"{mutator_aliases[node.func.id]}"
                )
                continue

            if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS:
                root = _function_state_root(
                    node.func.value,
                    imports=imports,
                    function_aliases=function_aliases,
                    state_aliases=state_aliases,
                )
                if root is not None:
                    findings.append(
                        f"line {node.lineno}: persistent function attribute mutation: "
                        f"{root}.{node.func.attr}"
                    )
                    continue

            target = _canonical_name(node.func, imports)
            if target in FUNCTION_MUTATORS and node.args:
                root = _function_state_root(
                    node.args[0],
                    imports=imports,
                    function_aliases=function_aliases,
                    state_aliases=state_aliases,
                )
                if root is not None:
                    findings.append(
                        f"line {node.lineno}: persistent function attribute mutation: "
                        f"{target}({root}, ...)"
                    )

    return sorted(set(findings))


def test_evaluation_package_has_no_persistent_function_attribute_state() -> None:
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
        ("def gate():\n    return True\ngate.cache = {}\n", "gate.cache"),
        ("def gate():\n    return True\ngate.count: int = 0\n", "gate.count"),
        ("def gate():\n    return True\ngate.count = 0\ngate.count += 1\n", "gate.count"),
        ("def gate():\n    return True\ngate.cache = {}\ndel gate.cache\n", "gate.cache"),
        ("def gate():\n    return True\ngate.cache = {}\ngate.cache['x'] = 1\n", "gate.cache"),
        ("def gate():\n    return True\ngate.cache = {}\ngate.cache.update({'x': 1})\n", "gate.cache.update"),
        ("def gate():\n    return True\nalias = gate\nalias.cache = []\n", "gate.cache"),
        (
            "def gate():\n    return True\ngate.cache = []\nstate = gate.cache\nstate.append(1)\n",
            "gate.cache.append",
        ),
        (
            "def gate():\n    return True\ngate.cache = {}\nmutate = gate.cache.update\nmutate({'x': 1})\n",
            "gate.cache.update",
        ),
        (
            "def gate():\n    return True\ngetattr(gate, 'cache').append(1)\n",
            "gate.cache.append",
        ),
        (
            "import builtins as b\ndef gate():\n    return True\nb.getattr(gate, 'cache').clear()\n",
            "gate.cache.clear",
        ),
        (
            "import operator\ndef gate():\n    return True\noperator.setitem(gate.cache, 'x', 1)\n",
            "operator.setitem(gate.cache",
        ),
        (
            "def gate():\n    return True\nlist.append(gate.cache, 1)\n",
            "list.append(gate.cache",
        ),
    ],
)
def test_detector_rejects_persistent_function_attribute_state(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "def gate():\n    local = {}\n    local['x'] = 1\n    return local\n",
        "def gate():\n    return True\nvalue = gate.__name__\n",
        "def gate():\n    return True\nvalue = getattr(gate, '__name__')\n",
        "def outer():\n    def local_gate():\n        return True\n    local_gate.cache = {}\n    return local_gate\n",
        "class Evaluator:\n    def gate(self):\n        self.cache = {}\n        return True\n",
    ],
)
def test_detector_allows_ephemeral_or_read_only_function_usage(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
