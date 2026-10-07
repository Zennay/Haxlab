from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

TRACKED_MODULES = {"builtins", "os", "resource"}

FORBIDDEN_CALLS = {
    "os.nice",
    "os.sched_setaffinity",
    "os.sched_setparam",
    "os.sched_setscheduler",
    "os.setpriority",
    "resource.setrlimit",
}

SPECIAL_CALL = "resource.prlimit"


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
    """Collect bindings owned by one function without entering nested scopes."""

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


class ResourceStateVisitor(ast.NodeVisitor):
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
                names.extend(ResourceStateVisitor._name_targets(element))
            return names
        return []

    @staticmethod
    def _is_trackable(target: str | None) -> bool:
        if not target:
            return False
        if target in FORBIDDEN_CALLS or target == SPECIAL_CALL:
            return True
        return target in {
            "builtins",
            "builtins.getattr",
            "getattr",
            "os",
            "resource",
        }

    @staticmethod
    def _prlimit_mutates(node: ast.Call) -> bool:
        if any(isinstance(argument, ast.Starred) for argument in node.args):
            return True
        if any(keyword.arg is None for keyword in node.keywords):
            return True
        if len(node.args) >= 3:
            third = node.args[2]
            return not (isinstance(third, ast.Constant) and third.value is None)
        for keyword in node.keywords:
            if keyword.arg == "limits":
                value = keyword.value
                return not (isinstance(value, ast.Constant) and value.value is None)
        return False

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
        for item in targets:
            for name in self._name_targets(item):
                self.aliases.bind(name, alias)

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
                        if root in {"os", "resource"}:
                            self.findings.append(
                                f"line {node.lineno}: ambiguous host resource/scheduler wildcard import: {node.module}"
                            )
                        continue
                    self.aliases.bind(
                        alias.asname or alias.name,
                        f"{node.module}.{alias.name}",
                    )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self._bind_assignment(list(node.targets), node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind_assignment([node.target], node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_assignment([node.target], node.value)

    def visit_Call(self, node: ast.Call) -> None:
        target = self.canonical(node.func)
        if target in FORBIDDEN_CALLS:
            self.findings.append(
                f"line {node.lineno}: host resource/scheduler mutation: {target}"
            )
        elif target == SPECIAL_CALL and self._prlimit_mutates(node):
            self.findings.append(
                f"line {node.lineno}: host resource/scheduler mutation: resource.prlimit"
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

        blocked = _function_local_names(node) | _argument_names(node.args)
        self.aliases.push(blocked)
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
    visitor = ResourceStateVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_package_does_not_mutate_host_resource_scheduler_state() -> None:
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
        ("import resource\nresource.setrlimit(resource.RLIMIT_CPU, (1, 1))\n", "resource.setrlimit"),
        ("import resource\nresource.prlimit(0, resource.RLIMIT_CPU, (1, 1))\n", "resource.prlimit"),
        ("import resource\nresource.prlimit(0, resource.RLIMIT_CPU, limits=(1, 1))\n", "resource.prlimit"),
        ("import os\nos.nice(1)\n", "os.nice"),
        ("import os\nos.setpriority(os.PRIO_PROCESS, 0, 10)\n", "os.setpriority"),
        ("import os\nos.sched_setaffinity(0, {0})\n", "os.sched_setaffinity"),
        ("import os\nos.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))\n", "os.sched_setscheduler"),
        ("import os\nos.sched_setparam(0, os.sched_param(0))\n", "os.sched_setparam"),
    ],
)
def test_detector_rejects_direct_resource_scheduler_mutation(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(source))
    assert expected in findings


def test_detector_rejects_alias_and_constant_getattr_bypasses() -> None:
    source = textwrap.dedent(
        """
        import os as operating_system
        import resource as limits
        from builtins import getattr as read_attr
        from resource import setrlimit as set_limit

        prioritize = operating_system.setpriority
        bind_cpu: object = getattr(operating_system, "sched_setaffinity")

        def probe():
            set_limit(limits.RLIMIT_CPU, (1, 1))
            prioritize(operating_system.PRIO_PROCESS, 0, 5)
            bind_cpu(0, {0})
            read_attr(limits, "prlimit")(0, limits.RLIMIT_CPU, (2, 2))
            (change := read_attr(operating_system, "nice"))(1)
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "resource.setrlimit",
        "os.setpriority",
        "os.sched_setaffinity",
        "resource.prlimit",
        "os.nice",
    ):
        assert expected in findings



def test_detector_rejects_destructured_aliases_and_ambiguous_prlimit_calls() -> None:
    source = textwrap.dedent(
        """
        import os
        import resource

        nice_fn, set_limit = os.nice, resource.setrlimit

        def probe(args, kwargs):
            nice_fn(1)
            set_limit(resource.RLIMIT_CPU, (1, 1))
            resource.prlimit(*args)
            resource.prlimit(0, resource.RLIMIT_CPU, **kwargs)
        """
    )

    findings = "\n".join(scan_source(source))
    assert "os.nice" in findings
    assert "resource.setrlimit" in findings
    assert findings.count("resource.prlimit") >= 2


def test_detector_rejects_tracked_wildcard_imports() -> None:
    findings = "\n".join(
        scan_source(
            textwrap.dedent(
                """
                from resource import *
                from os import *
                """
            )
        )
    )
    assert "wildcard import: resource" in findings
    assert "wildcard import: os" in findings

def test_detector_allows_read_only_resource_scheduler_queries() -> None:
    source = textwrap.dedent(
        """
        import os
        import resource

        def probe():
            soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
            current = resource.prlimit(0, resource.RLIMIT_CPU)
            current_none = resource.prlimit(0, resource.RLIMIT_CPU, None)
            current_kw = resource.prlimit(0, resource.RLIMIT_CPU, limits=None)
            priority = os.getpriority(os.PRIO_PROCESS, 0)
            affinity = os.sched_getaffinity(0)
            scheduler = os.sched_getscheduler(0)
            param = os.sched_getparam(0)
            return soft, hard, current, current_none, current_kw, priority, affinity, scheduler, param
        """
    )

    assert scan_source(source) == []


def test_detector_preserves_lexically_shadowed_names() -> None:
    source = textwrap.dedent(
        """
        import os
        import resource

        def probe(os, resource):
            os.nice(1)
            resource.setrlimit("local", "local")

        def local():
            setrlimit = lambda *args: args
            setrlimit("local", "local")
        """
    )

    assert scan_source(source) == []
