from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = (
    ROOT / "src" / "haxlab" / "ingestion",
    ROOT / "src" / "haxlab" / "learning",
    ROOT / "src" / "haxlab" / "runtime",
)
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
MUTATING_METHODS = {
    "__delitem__",
    "__iand__",
    "__ior__",
    "__isub__",
    "__ixor__",
    "__setitem__",
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
}


class _FunctionBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.locals: set[str] = set()
        self.globals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.locals.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.locals.difference_update(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.locals.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.locals.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.locals.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.locals.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.locals.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _function_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    collector = _FunctionBindingCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.locals, collector.globals


def _root_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return _root_name(node.value)
    return None


class GlobalMutationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.mutable_globals: set[str] = set()
        self.local_scopes: list[set[str]] = []
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
                if value is None or not self._contains_mutable_container(value):
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.mutable_globals.add(target.id)

        self.visit(tree)

    def visit_Global(self, node: ast.Global) -> None:
        self.violations.append(
            f"line {node.lineno}: runtime global rebinding is forbidden: {', '.join(node.names)}"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        capability = self._mutating_global_capability(node.func)
        if capability is not None:
            root, method = capability
            self.violations.append(
                f"line {node.lineno}: mutation of module-global {root!r} via .{method}() is forbidden"
            )
        else:
            target = self._qualified_name(node.func)
            if target and "." in target and node.args:
                owner, method = target.rsplit(".", 1)
                root = _root_name(node.args[0])
                if (
                    owner in MUTABLE_CONSTRUCTORS
                    and method in MUTATING_METHODS
                    and root
                    and self._is_live_mutable_global(root)
                ):
                    self.violations.append(
                        f"line {node.lineno}: functional mutation of module-global {root!r} via {target}() is forbidden"
                    )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_mutator_binding(node.value, node.lineno)
        for target in node.targets:
            self._check_mutating_target(target, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._check_mutator_binding(node.value, node.lineno)
        self._check_mutating_target(node.target, node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_mutating_target(node.target, node.lineno)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._check_mutating_target(target, node.lineno)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        assigned_here, globals_declared = _function_bindings(node)
        locals_here = set()
        args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if node.args.vararg is not None:
            args.append(node.args.vararg)
        if node.args.kwarg is not None:
            args.append(node.args.kwarg)
        locals_here.update(arg.arg for arg in args)
        locals_here.update(assigned_here - globals_declared)
        self.local_scopes.append(locals_here)
        for statement in node.body:
            self.visit(statement)
        self.local_scopes.pop()

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

    def _contains_mutable_container(self, node: ast.expr) -> bool:
        if isinstance(
            node,
            (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp),
        ):
            return True
        if isinstance(node, ast.Tuple):
            return any(self._contains_mutable_container(element) for element in node.elts)
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

    def _is_live_mutable_global(self, name: str) -> bool:
        if name not in self.mutable_globals:
            return False
        return not any(name in scope for scope in self.local_scopes)

    def _mutating_global_capability(
        self,
        node: ast.AST | None,
    ) -> tuple[str, str] | None:
        if isinstance(node, ast.Attribute) and node.attr in MUTATING_METHODS:
            root = _root_name(node.value)
            if root and self._is_live_mutable_global(root):
                return root, node.attr
        if isinstance(node, ast.Call):
            accessor = self._qualified_name(node.func)
            if (
                accessor in {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in MUTATING_METHODS
            ):
                root = _root_name(node.args[0])
                if root and self._is_live_mutable_global(root):
                    return root, node.args[1].value
        return None

    def _check_mutator_binding(self, value: ast.expr, lineno: int) -> None:
        capability = self._mutating_global_capability(value)
        if capability is None:
            return
        root, method = capability
        self.violations.append(
            f"line {lineno}: binding mutator .{method} of module-global {root!r} is forbidden"
        )

    def _check_mutating_target(self, target: ast.expr, lineno: int) -> None:
        if not isinstance(target, (ast.Subscript, ast.Attribute)):
            return
        root = _root_name(target)
        if root and self._is_live_mutable_global(root):
            self.violations.append(
                f"line {lineno}: item/attribute mutation of module-global {root!r} is forbidden"
            )


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = GlobalMutationVisitor()
    visitor.scan_module(tree)
    return sorted(set(visitor.violations))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def test_data_pipeline_auditors_do_not_mutate_module_global_state() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(path.read_text(encoding="utf-8"), filename=relative)
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    "source",
    [
        "CACHE = []\ndef audit():\n    CACHE.append('x')\n",
        "SEEN = set()\ndef audit():\n    SEEN.add('x')\n",
        "INDEX = {}\ndef audit():\n    INDEX['x'] = 1\n",
        "PAIR = ('stable', [])\ndef audit():\n    PAIR[1].extend([1])\n",
        "CACHE = {}\ndef audit():\n    CACHE.update({'x': 1})\n",
        "CACHE = []\ndef audit():\n    mutator = CACHE.append\n    mutator('x')\n",
        (
            "CACHE = set()\ndef audit():\n"
            "    getattr(CACHE, 'add')('x')\n"
        ),
        "CACHE = {}\ndef audit():\n    dict.update(CACHE, {'x': 1})\n",
        "VALUE = 0\ndef bump():\n    global VALUE\n    VALUE += 1\n",
    ],
)
def test_contract_rejects_runtime_module_global_mutation(source: str) -> None:
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "TOP_LEVEL_FIELDS = {'schema', 'replays'}\n",
        "SCHEMA_MAP = {'v1': 1}\n",
        "ROLES = ['gk', 'dm', 'am', 'st']\n",
        "ALLOWED = frozenset(('a', 'b'))\n",
        "CACHE = []\ndef build():\n    local = []\n    local.append(1)\n    return local\n",
        "CACHE = {}\ndef parse(CACHE):\n    CACHE['local'] = 1\n    return CACHE\n",
        "CACHE = {}\ndef parse():\n    CACHE = {}\n    CACHE['local'] = 1\n    return CACHE\n",
        (
            "CACHE = {}\ndef parse():\n"
            "    from payload_helpers import CACHE\n"
            "    CACHE.update({'local': 1})\n"
        ),
    ],
)
def test_contract_preserves_declarative_globals_and_local_mutability(
    source: str,
) -> None:
    assert scan_source(source) == []


def test_nested_scope_assignment_does_not_hide_outer_global_mutation() -> None:
    source = """
CACHE = []

def outer():
    def inner():
        CACHE = []
        return CACHE
    CACHE.append("x")
    return inner
"""
    findings = scan_source(source)
    assert any("CACHE" in finding and ".append()" in finding for finding in findings)
