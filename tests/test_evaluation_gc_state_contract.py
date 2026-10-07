from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "gc.disable",
    "gc.enable",
    "gc.freeze",
    "gc.set_debug",
    "gc.set_threshold",
    "gc.unfreeze",
}
CALLBACK_ROOT = "gc.callbacks"
CALLBACK_MUTATORS = {
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
    "__iadd__",
    "__imul__",
    "__setitem__",
    "__delitem__",
}
UNBOUND_MUTATORS = {
    "list.append",
    "list.clear",
    "list.extend",
    "list.insert",
    "list.pop",
    "list.remove",
    "list.reverse",
    "list.sort",
    "list.__iadd__",
    "list.__imul__",
    "list.__setitem__",
    "list.__delitem__",
    "operator.iadd",
    "operator.imul",
    "operator.setitem",
    "operator.delitem",
}
TRACKED_MODULES = {"builtins", "gc", "operator"}


class AliasState:
    def __init__(self) -> None:
        self.scopes: list[dict[str, str | None]] = [{}]

    def push(self, blocked: set[str] | None = None) -> None:
        scope: dict[str, str | None] = {}
        for name in blocked or set():
            scope[name] = None
        self.scopes.append(scope)

    def pop(self) -> None:
        self.scopes.pop()

    def bind(self, name: str, target: str | None) -> None:
        self.scopes[-1][name] = target

    def resolve(self, name: str) -> str:
        for scope in reversed(self.scopes):
            if name in scope:
                target = scope[name]
                return target if target is not None else f"<local:{name}>"
        return name


class _FunctionLocalCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def local_names(self) -> set[str]:
        return self.bound - self.global_names - self.nonlocal_names


def _function_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    collector = _FunctionLocalCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.local_names()


def _argument_names(args: ast.arguments) -> set[str]:
    names = {arg.arg for arg in args.posonlyargs}
    names.update(arg.arg for arg in args.args)
    names.update(arg.arg for arg in args.kwonlyargs)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


class GarbageCollectorStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases = AliasState()
        self.findings: list[str] = []

    def canonical(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.aliases.resolve(node.id)
        if isinstance(node, ast.Attribute):
            parent = self.canonical(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            accessor = self.canonical(node.func)
            if (
                accessor in {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                owner = self.canonical(node.args[0])
                if owner:
                    return f"{owner}.{node.args[1].value}"
        if isinstance(node, ast.Subscript):
            return self.canonical(node.value)
        if isinstance(node, ast.NamedExpr):
            return self.canonical(node.value)
        return None

    @staticmethod
    def _name_targets(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            names: list[str] = []
            for element in node.elts:
                names.extend(GarbageCollectorStateVisitor._name_targets(element))
            return names
        return []

    @staticmethod
    def _is_trackable(target: str | None) -> bool:
        if not target:
            return False
        if target in FORBIDDEN_CALLS or target == CALLBACK_ROOT:
            return True
        if target in UNBOUND_MUTATORS:
            return True
        if any(target == f"{CALLBACK_ROOT}.{method}" for method in CALLBACK_MUTATORS):
            return True
        return target in {
            "builtins",
            "builtins.delattr",
            "builtins.getattr",
            "builtins.setattr",
            "delattr",
            "gc",
            "getattr",
            "list",
            "operator",
            "setattr",
        }

    def _bind_pattern(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            resolved = self.canonical(value)
            self.aliases.bind(
                target.id,
                resolved if self._is_trackable(resolved) else None,
            )
            return

        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for target_item, value_item in zip(target.elts, value.elts, strict=True):
                self._bind_pattern(target_item, value_item)
            return

        for name in self._name_targets(target):
            self.aliases.bind(name, None)

    def _bind_assignment(self, targets: list[ast.AST], value: ast.AST) -> None:
        if len(targets) == 1:
            self._bind_pattern(targets[0], value)
            return
        resolved = self.canonical(value)
        alias = resolved if self._is_trackable(resolved) else None
        for target in targets:
            for name in self._name_targets(target):
                self.aliases.bind(name, alias)

    def _target_root(self, node: ast.AST) -> str | None:
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)):
            return self.canonical(node)
        return None

    def _record_target_mutation(
        self,
        node: ast.AST,
        target: ast.AST,
        *,
        allow_name: bool = False,
    ) -> None:
        if isinstance(target, ast.Name) and not allow_name:
            return
        root = self._target_root(target)
        if root == CALLBACK_ROOT:
            self.findings.append(
                f"line {getattr(node, 'lineno', 0)}: garbage-collector callback registry mutation"
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in TRACKED_MODULES:
                self.aliases.bind(alias.asname or root, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        if root == "gc":
                            self.findings.append(
                                f"line {node.lineno}: ambiguous garbage-collector wildcard import: {node.module}"
                            )
                        continue
                    self.aliases.bind(
                        alias.asname or alias.name,
                        f"{node.module}.{alias.name}",
                    )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._record_target_mutation(node, target)
        self._bind_assignment(list(node.targets), node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._record_target_mutation(node, node.target)
        if node.value is not None:
            self._bind_assignment([node.target], node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_assignment([node.target], node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._record_target_mutation(node, node.target, allow_name=True)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._record_target_mutation(node, target)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self.canonical(node.func)

        if target in FORBIDDEN_CALLS:
            self.findings.append(
                f"line {node.lineno}: garbage-collector process-state mutation: {target}"
            )
        elif (
            target
            and target.startswith(f"{CALLBACK_ROOT}.")
            and target.rsplit(".", 1)[-1] in CALLBACK_MUTATORS
        ):
            self.findings.append(
                f"line {node.lineno}: garbage-collector callback registry mutation: {target}"
            )
        elif target in UNBOUND_MUTATORS and node.args:
            owner = self.canonical(node.args[0])
            if owner == CALLBACK_ROOT:
                self.findings.append(
                    f"line {node.lineno}: garbage-collector callback registry mutation: {target}"
                )
        elif target in {
            "setattr",
            "delattr",
            "builtins.setattr",
            "builtins.delattr",
        } and len(node.args) >= 2:
            owner = self.canonical(node.args[0])
            attribute = node.args[1]
            if (
                owner == "gc"
                and isinstance(attribute, ast.Constant)
                and attribute.value == "callbacks"
            ):
                self.findings.append(
                    f"line {node.lineno}: garbage-collector callback registry reflective mutation"
                )

        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

        self.aliases.push(_function_local_names(node) | _argument_names(node.args))
        for statement in node.body:
            self.visit(statement)
        self.aliases.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        self.aliases.push(_argument_names(node.args))
        self.visit(node.body)
        self.aliases.pop()


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = GarbageCollectorStateVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_package_does_not_mutate_garbage_collector_state() -> None:
    failures: dict[str, list[str]] = {}
    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings
    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import gc\ngc.disable()\n", "gc.disable"),
        ("import gc\ngc.enable()\n", "gc.enable"),
        ("import gc\ngc.set_debug(gc.DEBUG_STATS)\n", "gc.set_debug"),
        ("import gc\ngc.set_threshold(700, 10, 10)\n", "gc.set_threshold"),
        ("import gc\ngc.freeze()\n", "gc.freeze"),
        ("import gc\ngc.unfreeze()\n", "gc.unfreeze"),
        ("import gc\ngc.callbacks.append(lambda phase, info: None)\n", "gc.callbacks.append"),
        ("import gc\ngc.callbacks[0] = lambda phase, info: None\n", "callback registry mutation"),
        ("import gc\ndel gc.callbacks[:]\n", "callback registry mutation"),
    ],
)
def test_detector_rejects_direct_garbage_collector_state_mutation(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(source))
    assert expected in findings


def test_detector_rejects_alias_reflection_and_unbound_mutator_bypasses() -> None:
    source = textwrap.dedent(
        """
        import gc as collector
        import operator
        from builtins import getattr as read_attr
        from gc import disable as stop_gc

        callbacks, threshold = collector.callbacks, collector.set_threshold
        append = callbacks.append

        def probe(callback):
            stop_gc()
            threshold(1, 1, 1)
            append(callback)
            read_attr(collector, "callbacks").clear()
            list.append(callbacks, callback)
            operator.setitem(callbacks, 0, callback)
            setattr(collector, "callbacks", [])
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "gc.disable",
        "gc.set_threshold",
        "gc.callbacks.append",
        "gc.callbacks.clear",
        "list.append",
        "operator.setitem",
        "reflective mutation",
    ):
        assert expected in findings


def test_detector_rejects_callback_alias_target_mutation_and_wildcard_import() -> None:
    source = textwrap.dedent(
        """
        import gc
        callbacks = gc.callbacks
        callbacks += []
        callbacks[0:0] = []
        del callbacks[:]
        from gc import *
        """
    )

    findings = "\n".join(scan_source(source))
    assert findings.count("callback registry mutation") >= 3
    assert "wildcard import: gc" in findings


def test_detector_allows_read_only_garbage_collector_access() -> None:
    source = textwrap.dedent(
        """
        import gc

        def probe():
            enabled = gc.isenabled()
            debug = gc.get_debug()
            threshold = gc.get_threshold()
            count = gc.get_count()
            stats = gc.get_stats()
            objects = gc.get_objects()
            callbacks = tuple(gc.callbacks)
            return enabled, debug, threshold, count, stats, len(objects), callbacks
        """
    )

    assert scan_source(source) == []


def test_detector_preserves_lexically_shadowed_gc_names() -> None:
    source = textwrap.dedent(
        """
        import gc

        def probe(gc):
            gc.disable()
            gc.callbacks.append("local")

        def local():
            disable = lambda: None
            callbacks = []
            disable()
            callbacks.append("local")
        """
    )

    assert scan_source(source) == []
