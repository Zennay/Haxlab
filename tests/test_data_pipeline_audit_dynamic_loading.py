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

_FORBIDDEN_CALLS = {
    "__import__",
    "builtins.__import__",
    "importlib.import_module",
    "importlib.reload",
    "importlib.util.module_from_spec",
    "importlib.util.spec_from_file_location",
    "pkgutil.resolve_name",
    "runpy.run_module",
    "runpy.run_path",
}
_FORBIDDEN_METHODS = {"exec_module", "load_module"}


def _qualname(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


class _AliasCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self.aliases[local] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for item in node.names:
            if item.name == "*":
                continue
            local = item.asname or item.name
            self.aliases[local] = f"{module}.{item.name}" if module else item.name


class _DynamicLoadingVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)
        if name in _FORBIDDEN_CALLS:
            self.violations.append(
                f"{node.lineno}:dynamic_loading_call:{name}"
            )
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_METHODS:
            self.violations.append(
                f"{node.lineno}:dynamic_loader_method:{node.func.attr}"
            )
        self.generic_visit(node)


def _dynamic_loading_violations(
    source: str,
    *,
    filename: str = "<contract>",
) -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _AliasCollector()
    aliases.visit(tree)
    visitor = _DynamicLoadingVisitor(aliases.aliases)
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
        ),
        key=lambda path: path.as_posix(),
    )


def test_data_pipeline_auditors_do_not_runtime_load_code() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline audit module"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for violation in _dynamic_loading_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        ):
            violations.append(f"{relative}:{violation}")

    assert violations == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("module = __import__(name)", "dynamic_loading_call:__import__"),
        (
            "from builtins import __import__ as load\nmodule = load(name)",
            "dynamic_loading_call:builtins.__import__",
        ),
        (
            "import importlib\nmodule = importlib.import_module(name)",
            "dynamic_loading_call:importlib.import_module",
        ),
        (
            "from importlib import import_module as load\nmodule = load(name)",
            "dynamic_loading_call:importlib.import_module",
        ),
        (
            "import importlib\nmodule = importlib.reload(existing)",
            "dynamic_loading_call:importlib.reload",
        ),
        (
            "from importlib.util import spec_from_file_location as make_spec\n"
            "spec = make_spec(name, path)",
            "dynamic_loading_call:importlib.util.spec_from_file_location",
        ),
        (
            "import importlib.util\nmodule = importlib.util.module_from_spec(spec)",
            "dynamic_loading_call:importlib.util.module_from_spec",
        ),
        (
            "spec.loader.exec_module(module)",
            "dynamic_loader_method:exec_module",
        ),
        (
            "loader.load_module(name)",
            "dynamic_loader_method:load_module",
        ),
        (
            "import runpy\nvalue = runpy.run_module(name)",
            "dynamic_loading_call:runpy.run_module",
        ),
        (
            "from runpy import run_path\nvalue = run_path(path)",
            "dynamic_loading_call:runpy.run_path",
        ),
        (
            "import pkgutil\nvalue = pkgutil.resolve_name(name)",
            "dynamic_loading_call:pkgutil.resolve_name",
        ),
    ],
)
def test_contract_rejects_runtime_loading_surfaces(
    source: str,
    reason: str,
) -> None:
    violations = _dynamic_loading_violations(source)
    assert any(reason in violation for violation in violations), violations


def test_contract_allows_static_imports_and_metadata_discovery() -> None:
    source = """
import importlib.metadata
import importlib.util
import json
from pathlib import Path

spec = importlib.util.find_spec("json")
version = importlib.metadata.version("package")
payload = json.loads(Path("evidence.json").read_text())
"""
    assert _dynamic_loading_violations(source) == []
