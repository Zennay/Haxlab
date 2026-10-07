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

UNBOUND_MUTATORS = {
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


def _assignment_name_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_assignment_name_targets(element))
        return names
    return []


def _import_aliases(tree: ast.Module) -> dict[str, str]:
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
                aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )
    return aliases


def _canonical_name(
    node: ast.AST | None,
    imports: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, imports)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _scope_nodes(root: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST, *, scope_root: bool = False) -> None:
        nodes.append(node)
        if not scope_root and isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(root, scope_root=True)
    return nodes


def _module_function_bindings(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {
        statement.name: statement.name
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for statement in tree.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Lambda):
            for target in statement.targets:
                for local in _assignment_name_targets(target):
                    bindings[local] = local
        elif (
            isinstance(statement, ast.AnnAssign)
            and statement.value is not None
            and isinstance(statement.value, ast.Lambda)
        ):
            for local in _assignment_name_targets(statement.target):
                bindings[local] = local

    changed = True
    while changed:
        changed = False
        for node in _scope_nodes(tree):
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
            root = bindings.get(value.id)
            if root is None:
                continue

            for target in targets:
                for local in _assignment_name_targets(target):
                    if bindings.get(local) != root:
                        bindings[local] = root
                        changed = True

    return bindings


def _function_locals(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> tuple[set[str], set[str]]:
    nodes = _scope_nodes(node)
    globals_: set[str] = set()
    for current in nodes:
        if isinstance(current, ast.Global):
            globals_.update(current.names)

    locals_: set[str] = set()
    arguments = node.args
    for argument in (
        list(arguments.posonlyargs)
        + list(arguments.args)
        + list(arguments.kwonlyargs)
    ):
        locals_.add(argument.arg)
    if arguments.vararg:
        locals_.add(arguments.vararg.arg)
    if arguments.kwarg:
        locals_.add(arguments.kwarg.arg)

    for current in nodes:
        if isinstance(current, ast.Name) and isinstance(current.ctx, ast.Store):
            if current.id not in globals_:
                locals_.add(current.id)

    return locals_, globals_


def _state_root(
    node: ast.AST | None,
    *,
    imports: dict[str, str],
    state_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None

    if isinstance(node, ast.Name):
        return state_aliases.get(node.id)

    if isinstance(node, ast.Attribute):
        parent = _state_root(
            node.value,
            imports=imports,
            state_aliases=state_aliases,
        )
        if parent is not None:
            return f"{parent}.{node.attr}"

    if isinstance(node, ast.Subscript):
        return _state_root(
            node.value,
            imports=imports,
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
            parent = _state_root(
                node.args[0],
                imports=imports,
                state_aliases=state_aliases,
            )
            if parent is not None:
                return f"{parent}.{node.args[1].value}"

    return None


def _scope_aliases(
    nodes: list[ast.AST],
    *,
    imports: dict[str, str],
    visible_bindings: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    state_aliases = dict(visible_bindings)
    mutator_aliases: dict[str, str] = {}

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

            state = _state_root(
                value,
                imports=imports,
                state_aliases=state_aliases,
            )

            bound_mutator: str | None = None
            if isinstance(value, ast.Attribute) and value.attr in MUTATING_METHODS:
                owner = _state_root(
                    value.value,
                    imports=imports,
                    state_aliases=state_aliases,
                )
                if owner is not None:
                    bound_mutator = f"{owner}.{value.attr}"
            elif (
                isinstance(value, ast.Call)
                and _canonical_name(value.func, imports)
                in {"getattr", "builtins.getattr"}
                and len(value.args) >= 2
                and isinstance(value.args[1], ast.Constant)
                and isinstance(value.args[1].value, str)
                and value.args[1].value in MUTATING_METHODS
            ):
                owner = _state_root(
                    value.args[0],
                    imports=imports,
                    state_aliases=state_aliases,
                )
                if owner is not None:
                    bound_mutator = f"{owner}.{value.args[1].value}"

            for target in targets:
                for local in _assignment_name_targets(target):
                    if state is not None and state_aliases.get(local) != state:
                        state_aliases[local] = state
                        changed = True
                    if (
                        bound_mutator is not None
                        and mutator_aliases.get(local) != bound_mutator
                    ):
                        mutator_aliases[local] = bound_mutator
                        changed = True

    return state_aliases, mutator_aliases


def _mutation_root(
    target: ast.AST,
    *,
    imports: dict[str, str],
    state_aliases: dict[str, str],
) -> str | None:
    if isinstance(target, ast.Attribute):
        parent = _state_root(
            target.value,
            imports=imports,
            state_aliases=state_aliases,
        )
        if parent is not None:
            return f"{parent}.{target.attr}"
    if isinstance(target, ast.Subscript):
        return _state_root(
            target.value,
            imports=imports,
            state_aliases=state_aliases,
        )
    return None


def _scan_scope(
    nodes: list[ast.AST],
    *,
    imports: dict[str, str],
    visible_bindings: dict[str, str],
) -> list[str]:
    state_aliases, mutator_aliases = _scope_aliases(
        nodes,
        imports=imports,
        visible_bindings=visible_bindings,
    )
    findings: list[str] = []

    for node in nodes:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            targets = []

        for target in targets:
            root = _mutation_root(
                target,
                imports=imports,
                state_aliases=state_aliases,
            )
            if root is not None:
                findings.append(
                    f"line {node.lineno}: persistent function attribute mutation: {root}"
                )

        if isinstance(node, ast.Delete):
            for target in node.targets:
                root = _mutation_root(
                    target,
                    imports=imports,
                    state_aliases=state_aliases,
                )
                if root is not None:
                    findings.append(
                        f"line {node.lineno}: persistent function attribute deletion: {root}"
                    )

        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name) and node.func.id in mutator_aliases:
            findings.append(
                f"line {node.lineno}: persistent function attribute mutation: "
                f"{mutator_aliases[node.func.id]}"
            )
            continue

        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS:
            root = _state_root(
                node.func.value,
                imports=imports,
                state_aliases=state_aliases,
            )
            if root is not None:
                findings.append(
                    f"line {node.lineno}: persistent function attribute mutation: "
                    f"{root}.{node.func.attr}"
                )
                continue

        target = _canonical_name(node.func, imports)
        normalized_target = (
            target.removeprefix("builtins.") if target is not None else None
        )
        if normalized_target in UNBOUND_MUTATORS and node.args:
            root = _state_root(
                node.args[0],
                imports=imports,
                state_aliases=state_aliases,
            )
            if root is not None:
                findings.append(
                    f"line {node.lineno}: persistent function attribute mutation: "
                    f"{normalized_target}({root}, ...)"
                )

    return findings


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    imports = _import_aliases(tree)
    module_bindings = _module_function_bindings(tree)
    findings: list[str] = []

    findings.extend(
        _scan_scope(
            _scope_nodes(tree),
            imports=imports,
            visible_bindings=module_bindings,
        )
    )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        locals_, globals_ = _function_locals(node)
        visible_bindings = {
            local: root
            for local, root in module_bindings.items()
            if local not in locals_ or local in globals_
        }
        findings.extend(
            _scan_scope(
                _scope_nodes(node),
                imports=imports,
                visible_bindings=visible_bindings,
            )
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
            "def gate():\n    return True\nother = gate\ndef mutate():\n    other.cache = {}\n",
            "gate.cache",
        ),
        (
            "def gate():\n    return True\ngate.cache = []\nstate = gate.cache\nstate.append(1)\n",
            "gate.cache.append",
        ),
        (
            "def gate():\n    return True\ngate.cache = {}\nmutate = gate.cache.update\nmutate({'x': 1})\n",
            "gate.cache.update",
        ),
        (
            "def gate():\n    return True\ngate.cache = {}\nmutate = getattr(gate.cache, 'update')\nmutate({'x': 1})\n",
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
        (
            "from builtins import list as MutableList\ndef gate():\n    return True\nMutableList.append(gate.cache, 1)\n",
            "list.append(gate.cache",
        ),
        (
            "def gate():\n    return True\ndef mutate():\n    global gate\n    gate.cache = {}\n",
            "gate.cache",
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
        "def gate():\n    return True\ndef helper(gate):\n    gate.cache = {}\n    return gate\n",
        "def gate():\n    return True\ndef helper():\n    gate = object()\n    gate.cache = {}\n    return gate\n",
        "def outer():\n    def local_gate():\n        return True\n    local_gate.cache = {}\n    return local_gate\n",
        "class Evaluator:\n    def gate(self):\n        self.cache = {}\n        return True\n",
    ],
)
def test_detector_allows_shadowed_ephemeral_or_read_only_function_usage(
    source: str,
) -> None:
    assert scan_source(textwrap.dedent(source)) == []
