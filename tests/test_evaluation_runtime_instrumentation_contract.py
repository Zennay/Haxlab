from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "sys.addaudithook",
    "faulthandler.enable",
    "faulthandler.disable",
    "faulthandler.register",
    "faulthandler.unregister",
    "faulthandler.dump_traceback_later",
    "faulthandler.cancel_dump_traceback_later",
    "tracemalloc.start",
    "tracemalloc.stop",
    "tracemalloc.reset_peak",
    "threading.settrace",
    "threading.setprofile",
    "threading.settrace_all_threads",
    "threading.setprofile_all_threads",
    "sys.monitoring.use_tool_id",
    "sys.monitoring.free_tool_id",
    "sys.monitoring.register_callback",
    "sys.monitoring.set_events",
    "sys.monitoring.set_local_events",
    "sys.monitoring.restart_events",
}

TRACKED_ROOTS = {"builtins", "faulthandler", "sys", "threading", "tracemalloc"}


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


class RuntimeInstrumentationVisitor(ast.NodeVisitor):
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
                names.extend(RuntimeInstrumentationVisitor._name_targets(element))
            return names
        return []

    @staticmethod
    def _trackable(target: str | None) -> bool:
        if not target:
            return False
        if target in FORBIDDEN_CALLS:
            return True
        return target in {
            "builtins",
            "builtins.getattr",
            "getattr",
            "faulthandler",
            "sys",
            "sys.monitoring",
            "threading",
            "tracemalloc",
        }

    def _bind_pattern(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            resolved = self.canonical(value)
            self.aliases.bind(target.id, resolved if self._trackable(resolved) else None)
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
        alias = resolved if self._trackable(resolved) else None
        for target in targets:
            for name in self._name_targets(target):
                self.aliases.bind(name, alias)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in TRACKED_ROOTS:
                self.aliases.bind(alias.asname or root, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_ROOTS:
                for alias in node.names:
                    if alias.name == "*":
                        if root in {"faulthandler", "sys", "threading", "tracemalloc"}:
                            self.findings.append(
                                f"line {node.lineno}: ambiguous instrumentation wildcard import: {node.module}"
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
                f"line {node.lineno}: process-wide instrumentation mutation: {target}"
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
    visitor = RuntimeInstrumentationVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_package_does_not_mutate_process_instrumentation_state() -> None:
    modules = sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sys\nsys.addaudithook(lambda event, args: None)\n", "sys.addaudithook"),
        ("import faulthandler\nfaulthandler.enable()\n", "faulthandler.enable"),
        ("import faulthandler\nfaulthandler.disable()\n", "faulthandler.disable"),
        (
            "import faulthandler\nfaulthandler.dump_traceback_later(1.0)\n",
            "faulthandler.dump_traceback_later",
        ),
        ("import tracemalloc\ntracemalloc.start()\n", "tracemalloc.start"),
        ("import tracemalloc\ntracemalloc.stop()\n", "tracemalloc.stop"),
        ("import tracemalloc\ntracemalloc.reset_peak()\n", "tracemalloc.reset_peak"),
        ("import threading\nthreading.settrace(lambda *a: None)\n", "threading.settrace"),
        ("import threading\nthreading.setprofile(lambda *a: None)\n", "threading.setprofile"),
        (
            "import sys\nsys.monitoring.set_events(1, 0)\n",
            "sys.monitoring.set_events",
        ),
        (
            "import sys\nsys.monitoring.register_callback(1, 2, lambda *a: None)\n",
            "sys.monitoring.register_callback",
        ),
    ],
)
def test_detector_rejects_direct_process_instrumentation_mutation(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(source))
    assert expected in findings


def test_detector_resolves_direct_import_assignment_and_getattr_aliases() -> None:
    source = textwrap.dedent(
        """
        import sys as runtime
        import faulthandler as faults
        import tracemalloc as tm
        from builtins import getattr as read_attr
        from threading import setprofile as install_profile

        audit = runtime.addaudithook
        monitoring = runtime.monitoring
        set_events = monitoring.set_events
        enable_faults = read_attr(faults, "enable")
        start_trace = getattr(tm, "start")

        def probe():
            audit(lambda event, args: None)
            set_events(1, 0)
            enable_faults()
            start_trace()
            install_profile(lambda *args: None)
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "sys.addaudithook",
        "sys.monitoring.set_events",
        "faulthandler.enable",
        "tracemalloc.start",
        "threading.setprofile",
    ):
        assert expected in findings


def test_detector_rejects_chained_and_tuple_aliases() -> None:
    source = textwrap.dedent(
        """
        import faulthandler
        import sys
        import threading

        first = second = faulthandler.disable
        monitor, trace_all = sys.monitoring.restart_events, threading.settrace_all_threads

        def probe():
            first()
            second()
            monitor()
            trace_all(lambda *args: None)
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "faulthandler.disable",
        "sys.monitoring.restart_events",
        "threading.settrace_all_threads",
    ):
        assert expected in findings


def test_detector_rejects_ambiguous_wildcard_instrumentation_imports() -> None:
    findings = "\n".join(
        scan_source(
            "from faulthandler import *\nfrom tracemalloc import *\nfrom threading import *\n"
        )
    )
    assert "wildcard import: faulthandler" in findings
    assert "wildcard import: tracemalloc" in findings
    assert "wildcard import: threading" in findings


def test_detector_allows_read_only_instrumentation_inspection() -> None:
    source = textwrap.dedent(
        """
        import faulthandler
        import threading
        import tracemalloc

        def probe():
            enabled = faulthandler.is_enabled()
            tracing = tracemalloc.is_tracing()
            memory = tracemalloc.get_traced_memory()
            trace_hook = threading.gettrace()
            profile_hook = threading.getprofile()
            return enabled, tracing, memory, trace_hook, profile_hook
        """
    )

    assert scan_source(source) == []


def test_detector_preserves_lexically_shadowed_names() -> None:
    source = textwrap.dedent(
        """
        import sys
        import tracemalloc

        def probe(sys, tracemalloc):
            sys.addaudithook("local")
            tracemalloc.start()

        def local():
            addaudithook = lambda callback: None
            start = lambda: None
            addaudithook(None)
            start()
        """
    )

    assert scan_source(source) == []
