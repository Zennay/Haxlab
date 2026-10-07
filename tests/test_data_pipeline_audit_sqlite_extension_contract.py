from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = (
    ROOT / "src" / "haxlab" / "ingestion",
    ROOT / "src" / "haxlab" / "learning",
    ROOT / "src" / "haxlab" / "runtime",
)
SQL_EXECUTION_METHODS = {"execute", "executemany", "executescript"}
SQLITE_EXTENSION_METHODS = {"enable_load_extension", "load_extension"}
LOAD_EXTENSION_SQL = re.compile(r"\bload_extension\s*\(", re.IGNORECASE)


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


def _is_extension_callable(name: str | None) -> bool:
    return bool(
        name
        and name.rsplit(".", 1)[-1] in SQLITE_EXTENSION_METHODS
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
            if _is_extension_callable(target) and aliases.get(local) != target:
                aliases[local] = target
                changed = True

    return aliases


def _literal_sql(call: ast.Call) -> str | None:
    if not call.args:
        return None
    node = call.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        target = _canonical_name(node.func, aliases)
        if _is_extension_callable(target):
            findings.append(
                f"line {node.lineno}: SQLite extension loading call: {target}"
            )

        method = target.rsplit(".", 1)[-1] if target else None
        if method in SQL_EXECUTION_METHODS:
            sql = _literal_sql(node)
            if sql is not None and LOAD_EXTENSION_SQL.search(sql):
                findings.append(
                    f"line {node.lineno}: SQLite load_extension SQL: {method}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_load_sqlite_extensions() -> None:
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
        (
            "def audit(connection):\n    connection.enable_load_extension(True)\n",
            "enable_load_extension",
        ),
        (
            "def audit(connection):\n    connection.load_extension('/tmp/ext.so')\n",
            "load_extension",
        ),
        (
            "def audit(connection):\n"
            "    loader = connection.load_extension\n"
            "    loader('/tmp/ext.so')\n",
            "load_extension",
        ),
        (
            "def audit(connection):\n"
            "    loader: object = getattr(connection, 'load_extension')\n"
            "    loader('/tmp/ext.so')\n",
            "load_extension",
        ),
        (
            "from builtins import getattr as read_attr\n"
            "def audit(connection):\n"
            "    toggle = read_attr(connection, 'enable_load_extension')\n"
            "    toggle(True)\n",
            "enable_load_extension",
        ),
        (
            "def audit(connection):\n"
            "    connection.execute(\"SELECT load_extension('/tmp/ext.so')\")\n",
            "load_extension SQL",
        ),
        (
            "def audit(connection):\n"
            "    connection.executemany('select LOAD_EXTENSION ( ? )', rows)\n",
            "load_extension SQL",
        ),
        (
            "def audit(connection):\n"
            "    connection.executescript('SELECT load_extension(\"x\");')\n",
            "load_extension SQL",
        ),
    ],
)
def test_contract_rejects_sqlite_extension_loading(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_read_only_sqlite_validation() -> None:
    source = """
def audit(connection):
    connection.execute("PRAGMA query_only=ON")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    rows = connection.execute("SELECT sha256 FROM evidence").fetchall()
    return quick_check, rows
"""
    assert scan_source(source) == []
