from __future__ import annotations

import ast
from pathlib import Path

EVALUATION_ROOT = Path(__file__).parents[1] / "src" / "haxlab" / "evaluation"

_FORBIDDEN_CALLS = {
    "sys.setrecursionlimit",
    "sys.setswitchinterval",
    "sys.set_int_max_str_digits",
}
_STACK_SIZE = "threading.stack_size"


class _InterpreterLimitScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scopes: list[dict[str, str | None]] = [{}]
        self.violations: list[tuple[int, str]] = []

    @property
    def aliases(self) -> dict[str, str | None]:
        return self.scopes[-1]

    def _lookup(self, name: str) -> str | None:
        return self.aliases.get(name, name)

    def _resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            base = self._resolve(node.args[0])
            return f"{base}.{node.args[1].value}" if base else None
        return None

    def _bind_target(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            self.aliases[target.id] = self._resolve(value) if value is not None else None
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, None)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".")[0]
            self.aliases[local] = item.name if item.asname else item.name.split(".")[0]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for item in node.names:
            if item.name == "*":
                continue
            self.aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._bind_target(node.target, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, node.value)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

        inherited = dict(self.aliases)
        self.scopes.append(inherited)
        arguments = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            self.aliases[argument.arg] = None
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        inherited = dict(self.aliases)
        self.scopes.append(inherited)
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            self.aliases[argument.arg] = None
        self.visit(node.body)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve(node.func)
        if name in _FORBIDDEN_CALLS:
            self.violations.append((node.lineno, name))
        elif name == _STACK_SIZE and (node.args or node.keywords):
            self.violations.append((node.lineno, name))
        self.generic_visit(node)


def _scan(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    scanner = _InterpreterLimitScanner()
    scanner.visit(tree)
    return scanner.violations


def test_evaluation_modules_do_not_mutate_interpreter_limits() -> None:
    modules = sorted(EVALUATION_ROOT.rglob("*.py"))
    assert modules, "evaluation source scan must be non-vacuous"

    violations: list[str] = []
    for path in modules:
        source = path.read_text(encoding="utf-8")
        for line, name in _scan(source):
            violations.append(f"{path.relative_to(EVALUATION_ROOT.parent.parent)}:{line}: {name}")

    assert violations == [], "interpreter-limit mutation detected:\n" + "\n".join(violations)


def test_direct_sys_setters_are_rejected() -> None:
    source = """
import sys
sys.setrecursionlimit(4096)
sys.setswitchinterval(0.01)
sys.set_int_max_str_digits(10000)
"""
    assert [name for _, name in _scan(source)] == [
        "sys.setrecursionlimit",
        "sys.setswitchinterval",
        "sys.set_int_max_str_digits",
    ]


def test_import_assignment_and_getattr_aliases_are_rejected() -> None:
    source = """
import sys as runtime
from sys import setswitchinterval as tune
recursion = runtime.setrecursionlimit
digits = getattr(runtime, "set_int_max_str_digits")
recursion(4096)
tune(0.01)
digits(10000)
"""
    assert {name for _, name in _scan(source)} == {
        "sys.setrecursionlimit",
        "sys.setswitchinterval",
        "sys.set_int_max_str_digits",
    }


def test_thread_stack_size_setter_is_rejected_but_getter_is_allowed() -> None:
    source = """
import threading as th
from threading import stack_size as stack
current = th.stack_size()
th.stack_size(262144)
setter = stack
setter(524288)
"""
    assert [name for _, name in _scan(source)] == [
        "threading.stack_size",
        "threading.stack_size",
    ]


def test_read_only_interpreter_inspection_is_allowed() -> None:
    source = """
import sys
from threading import stack_size
sys.getrecursionlimit()
sys.getswitchinterval()
sys.get_int_max_str_digits()
stack_size()
"""
    assert _scan(source) == []


def test_function_arguments_shadow_import_aliases() -> None:
    source = """
import sys
def inspect(sys):
    sys.setrecursionlimit(1)
"""
    assert _scan(source) == []
