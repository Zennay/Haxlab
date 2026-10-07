from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BANNED_IMPORT_PREFIXES = (
    "_ctypes",
    "cffi",
    "ctypes",
    "numpy.ctypeslib",
)

BANNED_NATIVE_LOADER_CALLS = {
    "numpy.ctypeslib.load_library",
    "tensorflow.load_op_library",
    "torch.classes.load_library",
    "torch.ops.load_library",
    "torch.utils.cpp_extension.load",
    "torch.utils.cpp_extension.load_inline",
}


def _matches_banned_import(name: str) -> bool:
    return any(
        name == prefix or name.startswith(f"{prefix}.")
        for prefix in BANNED_IMPORT_PREFIXES
    )


class NativeFFIVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
            if _matches_banned_import(alias.name):
                self.violations.append(
                    f"line {node.lineno}: native FFI import is forbidden: {alias.name!r}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            self.generic_visit(node)
            return

        if _matches_banned_import(node.module):
            self.violations.append(
                f"line {node.lineno}: native FFI import is forbidden: {node.module!r}"
            )

        for alias in node.names:
            if alias.name == "*":
                continue
            qualified = f"{node.module}.{alias.name}"
            self.aliases[alias.asname or alias.name] = qualified
            if _matches_banned_import(qualified):
                self.violations.append(
                    f"line {node.lineno}: native FFI import is forbidden: {qualified!r}"
                )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        resolved = self._qualified_name(node.value)
        if resolved is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = resolved
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            resolved = self._qualified_name(node.value)
            if resolved is not None:
                self.aliases[node.target.id] = resolved
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target in BANNED_NATIVE_LOADER_CALLS:
            self.violations.append(
                f"line {node.lineno}: native library loading is forbidden: {target!r}"
            )
        self.generic_visit(node)

    def _qualified_name(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            return None if parent is None else f"{parent}.{node.attr}"
        if isinstance(node, ast.Call):
            accessor = self._qualified_name(node.func)
            if (
                accessor in {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                owner = self._qualified_name(node.args[0])
                if owner:
                    return f"{owner}.{node.args[1].value}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = NativeFFIVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def test_evaluation_package_has_no_native_ffi_escape_hatches() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import ctypes\n", "ctypes"),
        ("from ctypes import CDLL as load\n", "ctypes"),
        ("import _ctypes\n", "_ctypes"),
        ("import cffi as ffi\n", "cffi"),
        ("from numpy import ctypeslib\n", "numpy.ctypeslib"),
        (
            "import numpy as np\nnp.ctypeslib.load_library('agent', '.')\n",
            "numpy.ctypeslib.load_library",
        ),
        (
            "import numpy as np\nloader = np.ctypeslib.load_library\nloader('agent', '.')\n",
            "numpy.ctypeslib.load_library",
        ),
        (
            "from torch import ops as native_ops\nnative_ops.load_library('agent.so')\n",
            "torch.ops.load_library",
        ),
        (
            "from torch.utils.cpp_extension import load as build\nbuild(name='agent', sources=[])\n",
            "torch.utils.cpp_extension.load",
        ),
        (
            "import torch\ngetattr(torch.ops, 'load_library')('agent.so')\n",
            "torch.ops.load_library",
        ),
        (
            "import tensorflow as tf\ntf.load_op_library('agent.so')\n",
            "tensorflow.load_op_library",
        ),
    ],
)
def test_detector_rejects_native_ffi_and_library_loading(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(source))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import numpy as np\nnp.asarray([1.0, 2.0])\n",
        "import torch\nwith torch.no_grad():\n    value = 1\n",
        "from pathlib import Path\nPath('evidence.json').read_text(encoding='utf-8')\n",
        "import importlib.util\nimportlib.util.find_spec('json')\n",
        "from haxlab.evaluation.models import PromotionDecision\n",
    ],
)
def test_detector_allows_normal_non_loader_evaluation_code(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
