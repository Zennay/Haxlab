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
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            for alias in statement.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = (
                    f"{statement.module}.{alias.name}"
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


def _module_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                roots.update(_assignment_name_targets(target))
        elif isinstance(statement, ast.AnnAssign):
            roots.update(_assignment_name_targets(statement.target))
    return roots


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
    for arg in (
        list(arguments.posonlyargs)
        + list(arguments.args)
        + list(arguments.kwonlyargs)
    ):
        locals_.add(arg.arg)
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
    visible_roots: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    state_aliases = {name: name for name in visible_roots}
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
    visible_roots: set[str],
) -> list[str]:
    state_aliases, mutator_aliases = _scope_aliases(
        nodes,
        imports=imports,
        visible_roots=visible_roots,
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
                    f"line {node.lineno}: persistent module object mutation: {root}"
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
                        f"line {node.lineno}: persistent module object deletion: {root}"
                    )

        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name) and node.func.id in mutator_aliases:
            findings.append(
                f"line {node.lineno}: persistent module object mutation: "
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
                    f"line {node.lineno}: persistent module object mutation: "
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
                    f"line {node.lineno}: persistent module object mutation: "
                    f"{normalized_target}({root}, ...)"
                )

    return findings


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    imports = _import_aliases(tree)
    module_roots = _module_roots(tree)
    findings: list[str] = []

    findings.extend(
        _scan_scope(
            _scope_nodes(tree),
            imports=imports,
            visible_roots=module_roots,
        )
    )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        locals_, globals_ = _function_locals(node)
        visible_roots = {
            root
            for root in module_roots
            if root not in locals_ or root in globals_
        }
        findings.extend(
            _scan_scope(
                _scope_nodes(node),
                imports=imports,
                visible_roots=visible_roots,
            )
        )

    return sorted(set(findings))


def test_evaluation_package_has_no_persistent_module_object_state() -> None:
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
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    STATE.cache = {}\n",
            "STATE.cache",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    STATE.count: int = 0\n",
            "STATE.count",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    STATE.count += 1\n",
            "STATE.count",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    del STATE.cache\n",
            "STATE.cache",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    STATE.cache['x'] = 1\n",
            "STATE.cache",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    STATE.cache.update({'x': 1})\n",
            "STATE.cache.update",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    alias = STATE\n    alias.cache = []\n",
            "STATE.cache",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    cache = STATE.cache\n    cache.append(1)\n",
            "STATE.cache.append",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    mutate = STATE.cache.update\n    mutate({'x': 1})\n",
            "STATE.cache.update",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    getattr(STATE, 'cache').clear()\n",
            "STATE.cache.clear",
        ),
        (
            "import builtins as b\nclass Holder: pass\nSTATE = Holder()\ndef gate():\n    b.getattr(STATE, 'cache').append(1)\n",
            "STATE.cache.append",
        ),
        (
            "import operator\nclass Holder: pass\nSTATE = Holder()\ndef gate():\n    operator.setitem(STATE.cache, 'x', 1)\n",
            "operator.setitem(STATE.cache",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    mutate = getattr(STATE.cache, 'update')\n    mutate({'x': 1})\n",
            "STATE.cache.update",
        ),
        (
            "class Holder: pass\nSTATE = Holder()\ndef gate():\n    list.append(STATE.cache, 1)\n",
            "list.append(STATE.cache",
        ),
        (
            "from builtins import list as MutableList\nclass Holder: pass\nSTATE = Holder()\ndef gate():\n    MutableList.append(STATE.cache, 1)\n",
            "list.append(STATE.cache",
        ),
    ],
)
def test_detector_rejects_persistent_module_object_state(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "class Policy:\n    threshold = 0.5\nPOLICY = Policy()\ndef gate():\n    return POLICY.threshold\n",
        "class Holder: pass\nSTATE = Holder()\ndef gate():\n    local = Holder()\n    local.cache = {}\n    return local\n",
        "class Holder: pass\nSTATE = Holder()\ndef gate(STATE):\n    STATE.cache = {}\n    return STATE\n",
        "class Evaluator:\n    def gate(self):\n        self.cache = {}\n        return True\n",
        "def gate():\n    return True\ngate.cache = {}\n",
        "class Evaluator:\n    cache = {}\n",
        "class Holder: pass\nSTATE = Holder()\ndef outer():\n    def inner():\n        local = Holder()\n        local.cache = {}\n        return local\n    return inner()\n",
    ],
)
def test_detector_allows_non_owned_or_ephemeral_state(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
