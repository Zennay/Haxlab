from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

DYNAMIC_LOADING_CALLS = {
    "__import__",
    "builtins.__import__",
    "importlib.import_module",
    "importlib.util.module_from_spec",
    "importlib.util.spec_from_file_location",
    "importlib.machinery.ExtensionFileLoader",
    "importlib.machinery.SourceFileLoader",
    "importlib.machinery.SourcelessFileLoader",
    "pkgutil.resolve_name",
    "runpy.run_module",
    "runpy.run_path",
}


class DynamicLoadingVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                self.aliases[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target in DYNAMIC_LOADING_CALLS:
            self.violations.append(
                f"line {node.lineno}: dynamic module/code loading is forbidden: {target!r}"
            )
        self.generic_visit(node)

    def _qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            if parent is None:
                return None
            return f"{parent}.{node.attr}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = DynamicLoadingVisitor()
    visitor.visit(tree)
    return visitor.violations


def test_evaluation_package_has_no_dynamic_module_loading() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("__import__('haxlab.runtime')\n", "__import__"),
        ("from builtins import __import__ as load\nload('haxlab.runtime')\n", "builtins.__import__"),
        ("import importlib\nimportlib.import_module('haxlab.runtime')\n", "importlib.import_module"),
        ("from importlib import import_module as load\nload('haxlab.runtime')\n", "importlib.import_module"),
        ("import importlib.util as iu\niu.spec_from_file_location('x', 'x.py')\n", "spec_from_file_location"),
        ("from importlib.util import module_from_spec as build\nbuild(spec)\n", "module_from_spec"),
        ("from importlib.machinery import SourceFileLoader as Loader\nLoader('x', 'x.py')\n", "SourceFileLoader"),
        ("import runpy\nrunpy.run_module('haxlab.runtime')\n", "runpy.run_module"),
        ("from runpy import run_path\nrun_path('script.py')\n", "runpy.run_path"),
        ("import pkgutil as p\np.resolve_name('haxlab.runtime:state')\n", "pkgutil.resolve_name"),
    ],
)
def test_detector_rejects_dynamic_loading(source: str, expected: str) -> None:
    violations = scan_source(source)
    assert any(expected in violation for violation in violations), violations


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.loads('{}')\n",
        "from haxlab.evaluation.models import PromotionDecision\n",
        "import importlib.util\nimportlib.util.find_spec('json')\n",
        "import pkgutil\nlist(pkgutil.iter_modules([]))\n",
        "from pathlib import Path\nPath('evidence.json').read_text(encoding='utf-8')\n",
    ],
)
def test_detector_allows_static_imports_and_non_loading_metadata(source: str) -> None:
    assert scan_source(source) == []
