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
FORBIDDEN_IMPORT_ROOTS = {"_ctypes", "ctypes", "cffi"}
FORBIDDEN_CALLS = {
    "cffi.FFI",
    "_ctypes.dlopen",
    "ctypes.CDLL",
    "ctypes.OleDLL",
    "ctypes.PyDLL",
    "ctypes.WinDLL",
    "ctypes.cdll.LoadLibrary",
    "ctypes.pydll.LoadLibrary",
    "numpy.ctypeslib.load_library",
    "torch.ops.load_library",
}
FORBIDDEN_METHODS = {"LoadLibrary", "dlopen"}


def _audit_paths() -> list[Path]:
    return sorted(
        {
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        },
        key=lambda path: path.as_posix(),
    )


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _canonical_name(node.value, aliases)
        return f"{owner}.{node.attr}" if owner else node.attr
    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _canonical_name(node.args[0], aliases)
            if owner:
                return f"{owner}.{node.args[1].value}"
        if accessor == "cffi.FFI" and not node.args and not node.keywords:
            return "cffi.FFI()"
    return None


def _simple_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return node.target.id, node.value
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None


def _tracked_alias(name: str | None) -> bool:
    if not name:
        return False
    return (
        name in FORBIDDEN_CALLS
        or name == "cffi.FFI()"
        or name.rsplit(".", 1)[-1] in FORBIDDEN_METHODS
    )


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else item.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                if item.name == "*":
                    continue
                local = item.asname or item.name
                aliases[local] = f"{module}.{item.name}" if module else item.name

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            target = _canonical_name(expression, aliases)
            if _tracked_alias(target) and aliases.get(local) != target:
                aliases[local] = target
                changed = True

    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                root = item.name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    findings.append(
                        f"line {node.lineno}: native FFI import: {item.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                findings.append(
                    f"line {node.lineno}: native FFI import: {node.module or root}"
                )
        elif isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if (
                target in FORBIDDEN_CALLS
                or (
                    target
                    and target.rsplit(".", 1)[-1] in FORBIDDEN_METHODS
                )
            ):
                findings.append(
                    f"line {node.lineno}: native FFI/library loading call: {target}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_have_no_native_ffi_loading() -> None:
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
    ("source", "expected"),
    [
        ("import _ctypes\n", "native FFI import: _ctypes"),
        ("import ctypes\n", "native FFI import: ctypes"),
        ("import cffi as ffi_lib\n", "native FFI import: cffi"),
        ("from ctypes import CDLL\n", "native FFI import: ctypes"),
        (
            "import ctypes\ndef audit():\n    ctypes.CDLL('/tmp/lib.so')\n",
            "ctypes.CDLL",
        ),
        (
            "import ctypes as ffi\ndef audit():\n    ffi.PyDLL('/tmp/lib.so')\n",
            "ctypes.PyDLL",
        ),
        (
            "import ctypes\ndef audit():\n    loader = ctypes.CDLL\n    loader('/tmp/lib.so')\n",
            "ctypes.CDLL",
        ),
        (
            "import ctypes\ndef audit():\n"
            "    loader = getattr(ctypes, 'CDLL')\n"
            "    loader('/tmp/lib.so')\n",
            "ctypes.CDLL",
        ),
        (
            "from builtins import getattr as read_attr\nimport ctypes\n"
            "def audit():\n"
            "    loader = read_attr(ctypes.cdll, 'LoadLibrary')\n"
            "    loader('/tmp/lib.so')\n",
            "ctypes.cdll.LoadLibrary",
        ),
        (
            "import cffi\ndef audit():\n"
            "    ffi = cffi.FFI()\n"
            "    ffi.dlopen('/tmp/lib.so')\n",
            "cffi.FFI().dlopen",
        ),
        (
            "import _ctypes\ndef audit():\n    _ctypes.dlopen('/tmp/lib.so')\n",
            "_ctypes.dlopen",
        ),
        (
            "import numpy as np\ndef audit():\n"
            "    np.ctypeslib.load_library('libprobe', '/tmp')\n",
            "numpy.ctypeslib.load_library",
        ),
        (
            "import torch\ndef audit():\n    torch.ops.load_library('/tmp/lib.so')\n",
            "torch.ops.load_library",
        ),
        (
            "def audit(loader):\n    loader.dlopen('/tmp/lib.so')\n",
            "loader.dlopen",
        ),
    ],
)
def test_contract_rejects_native_ffi_surfaces(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_pure_python_local_evidence_validation() -> None:
    source = """
import hashlib
import json
from pathlib import Path

def audit(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
"""
    assert scan_source(source) == []
