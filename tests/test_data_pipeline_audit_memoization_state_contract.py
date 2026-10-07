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
STATEFUL_MEMOIZERS = {
    "functools.cache",
    "functools.cached_property",
    "functools.lru_cache",
}


class MemoizationStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scopes: list[dict[str, str | None]] = [{}]
        self.violations: list[str] = []

    def _bind(self, name: str, target: str | None) -> None:
        self.scopes[-1][name] = target

    def _lookup(self, name: str) -> str:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name] or name
        return name

    def _qualified_name(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            if parent is None:
                return None
            return f"{parent}.{node.attr}"
        if (
            isinstance(node, ast.Call)
            and self._qualified_name(node.func) in {"getattr", "builtins.getattr"}
            and len(node.args) == 2
            and not node.keywords
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            parent = self._qualified_name(node.args[0])
            if parent is None:
                return None
            return f"{parent}.{node.args[1].value}"
        return None

    def _bind_target(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, self._qualified_name(value))
            return
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for target_item, value_item in zip(target.elts, value.elts, strict=True):
                self._bind_target(target_item, value_item)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._bind_target(item, None)

    def _check_memoizer(self, node: ast.AST, target: ast.AST | None) -> None:
        name = self._qualified_name(target)
        if name in STATEFUL_MEMOIZERS:
            self.violations.append(
                f"line {node.lineno}: stateful memoization is forbidden: {name}"
            )

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            self._check_memoizer(decorator, target)
            self.visit(decorator)

        self._bind(node.name, None)
        local: dict[str, str | None] = {}
        args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if node.args.vararg is not None:
            args.append(node.args.vararg)
        if node.args.kwarg is not None:
            args.append(node.args.kwarg)
        for arg in args:
            local[arg.arg] = None

        self.scopes.append(local)
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self._bind(local, target)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for item in node.names:
            if item.name == "*":
                continue
            self._bind(item.asname or item.name, f"{node.module}.{item.name}")

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind_target(target, node.value)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind_target(node.target, node.value)
        if node.value is not None:
            self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_target(node.target, node.value)
        self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        local = {arg.arg: None for arg in (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )}
        if node.args.vararg is not None:
            local[node.args.vararg.arg] = None
        if node.args.kwarg is not None:
            local[node.args.kwarg.arg] = None
        self.scopes.append(local)
        self.visit(node.body)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self._check_memoizer(node, node.func)
        self.generic_visit(node)


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = MemoizationStateVisitor()
    visitor.visit(tree)
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


def test_data_pipeline_auditors_do_not_retain_memoized_state() -> None:
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
        "import functools\n@functools.cache\ndef audit(path):\n    return path\n",
        "from functools import lru_cache\n@lru_cache()\ndef audit(path):\n    return path\n",
        "from functools import cached_property\nclass Audit:\n    @cached_property\n    def verdict(self):\n        return True\n",
        "import functools as ft\nwrapped = ft.cache(lambda value: value)\n",
        "from functools import lru_cache as memo\nwrapped = memo(maxsize=8)(lambda value: value)\n",
        "import functools\nmemo = functools.cache\nwrapped = memo(lambda value: value)\n",
        "import functools\nmemo = getattr(functools, 'lru_cache')\nwrapped = memo()(lambda value: value)\n",
        "import builtins, functools\nga = builtins.getattr\nmemo = ga(functools, 'cache')\nwrapped = memo(lambda value: value)\n",
        "import functools\na, memo = object(), functools.cache\nwrapped = memo(lambda value: value)\n",
    ],
)
def test_contract_rejects_stateful_memoization(source: str) -> None:
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "import functools\nkey = functools.partial(str.lower)\n",
        "from functools import wraps\ndef deco(fn):\n    @wraps(fn)\n    def inner(*args, **kwargs):\n        return fn(*args, **kwargs)\n    return inner\n",
        "def audit(cache):\n    return cache('explicit-input')\n",
        "cache = 'declarative schema label'\ndef audit(value):\n    return value\n",
    ],
)
def test_contract_preserves_stateless_helpers_and_shadowing(source: str) -> None:
    assert scan_source(source) == []
