from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = tuple(
    ROOT / "src" / "haxlab" / name
    for name in ("ingestion", "learning", "runtime")
)
FORBIDDEN_CALLS = {
    "atexit.register",
    "atexit.unregister",
    "atexit._clear",
    "atexit._run_exitfuncs",
}


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
    return None


def _assignment_pairs(node: ast.AST) -> list[tuple[str, ast.AST]]:
    if isinstance(node, ast.Assign):
        pairs: list[tuple[str, ast.AST]] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                pairs.append((target.id, node.value))
            elif (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(node.value, (ast.Tuple, ast.List))
                and len(target.elts) == len(node.value.elts)
            ):
                pairs.extend(
                    (left.id, right)
                    for left, right in zip(target.elts, node.value.elts, strict=True)
                    if isinstance(left, ast.Name)
                )
        return pairs
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return [(node.target.id, node.value)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.value)]
    return []


def _tracked(name: str | None) -> bool:
    return bool(
        name
        and (
            name == "atexit"
            or name in FORBIDDEN_CALLS
            or name.startswith("atexit.")
        )
    )


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = (
                    item.name if item.asname else item.name.split(".", 1)[0]
                )
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
            for local, expression in _assignment_pairs(node):
                target = _canonical_name(expression, aliases)
                if _tracked(target) and aliases.get(local) != target:
                    aliases[local] = target
                    changed = True
    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "atexit"
            and any(item.name == "*" for item in node.names)
        ):
            findings.append(
                f"line {node.lineno}: wildcard atexit import exposes registry mutators"
            )
        elif isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: atexit registry mutation: {target}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_mutate_atexit_registry() -> None:
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
        ("import atexit\natexit.register(lambda: None)\n", "atexit.register"),
        (
            "from atexit import unregister as remove\nremove(lambda: None)\n",
            "atexit.unregister",
        ),
        (
            "import atexit as ae\nclear = ae._clear\nclear()\n",
            "atexit._clear",
        ),
        (
            "import atexit\ngetattr(atexit, '_run_exitfuncs')()\n",
            "atexit._run_exitfuncs",
        ),
        (
            "import atexit\na = b = atexit.register\na(lambda: None)\n",
            "atexit.register",
        ),
        (
            "import atexit\nreg, clear = atexit.register, atexit._clear\nclear()\n",
            "atexit._clear",
        ),
        ("from atexit import *\n", "wildcard atexit import"),
    ],
)
def test_contract_rejects_atexit_registry_mutation(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_local_callback_registries() -> None:
    source = """
callbacks: list[object] = []

def register(callback: object) -> None:
    callbacks.append(callback)

def audit(callback: object) -> int:
    register(callback)
    return len(callbacks)
"""
    assert scan_source(source) == []
