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

_WRITE_OS_FLAGS = {
    "os.O_WRONLY",
    "os.O_RDWR",
    "os.O_APPEND",
    "os.O_CREAT",
    "os.O_TRUNC",
}
_MUTATING_CALLS = {
    "json.dump",
    "numpy.save",
    "numpy.savez",
    "numpy.savez_compressed",
    "numpy.savetxt",
    "os.chmod",
    "os.chown",
    "os.fchmod",
    "os.fchown",
    "os.link",
    "os.makedirs",
    "os.mkdir",
    "os.mknod",
    "os.remove",
    "os.removedirs",
    "os.rename",
    "os.renames",
    "os.replace",
    "os.rmdir",
    "os.symlink",
    "os.truncate",
    "os.unlink",
    "pickle.dump",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copymode",
    "shutil.copystat",
    "shutil.copytree",
    "shutil.move",
    "shutil.rmtree",
    "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryDirectory",
    "tempfile.TemporaryFile",
    "tempfile.mkdtemp",
    "tempfile.mkstemp",
    "torch.save",
}
_MUTATING_METHOD_NAMES = {
    "chmod",
    "hardlink_to",
    "lchmod",
    "mkdir",
    "rename",
    "rmdir",
    "symlink_to",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
}
_DIRECT_WRITE_METHODS = {"truncate", "write", "writelines"}
_SQL_MUTATION_PREFIXES = {
    "ALTER",
    "ATTACH",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "INSERT",
    "REINDEX",
    "REPLACE",
    "UPDATE",
    "VACUUM",
}


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


def _literal_mode(call: ast.Call, *, positional_index: int = 1) -> str | None:
    node: ast.AST | None = None
    if len(call.args) > positional_index:
        node = call.args[positional_index]
    else:
        for keyword in call.keywords:
            if keyword.arg == "mode":
                node = keyword.value
                break
    if node is None:
        return "r"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_write_mode(mode: str) -> bool:
    return any(marker in mode for marker in ("w", "a", "x", "+"))


def _literal_sql(call: ast.Call) -> str | None:
    if not call.args:
        return None
    node = call.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _AuditMutationVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.violations: list[str] = []

    def _add(self, node: ast.AST, reason: str) -> None:
        self.violations.append(f"{getattr(node, 'lineno', 0)}:{reason}")

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            name = _qualname(node, self.aliases)
            if name in _WRITE_OS_FLAGS:
                self._add(node, f"write_os_flag:{name}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            name = _qualname(node, self.aliases)
            if name in _WRITE_OS_FLAGS:
                self._add(node, f"write_os_flag:{name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None

        if name in _MUTATING_CALLS:
            self._add(node, f"mutating_call:{name}")

        if attr in _MUTATING_METHOD_NAMES:
            self._add(node, f"mutating_method:{attr}")

        if attr in _DIRECT_WRITE_METHODS:
            self._add(node, f"direct_write_method:{attr}")

        is_builtin_open = name in {"open", "builtins.open"}
        is_method_open = attr == "open" and name != "os.open"
        is_fdopen = name == "os.fdopen"
        if is_builtin_open or is_method_open or is_fdopen:
            mode = _literal_mode(node)
            if mode is None:
                self._add(node, "dynamic_file_mode")
            elif _is_write_mode(mode):
                self._add(node, f"write_file_mode:{mode}")

        if name == "os.open" and len(node.args) > 1:
            flags = node.args[1]
            if isinstance(flags, ast.Constant) and type(flags.value) is int and flags.value != 0:
                self._add(node, f"numeric_os_open_flags:{flags.value}")

        if attr in {"execute", "executemany", "executescript"}:
            sql = _literal_sql(node)
            if sql is not None:
                stripped = sql.lstrip()
                first = stripped.split(None, 1)[0].upper() if stripped else ""
                if first in _SQL_MUTATION_PREFIXES:
                    self._add(node, f"mutating_sql:{first}")
                if first == "PRAGMA" and "=" in stripped:
                    normalized = stripped.upper().replace(" ", "")
                    if not normalized.startswith("PRAGMAQUERY_ONLY="):
                        self._add(node, "mutating_sql:PRAGMA")

        self.generic_visit(node)


def _mutation_violations(source: str, *, filename: str = "<contract>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _AliasCollector()
    aliases.visit(tree)
    visitor = _AuditMutationVisitor(aliases.aliases)
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


def test_data_pipeline_auditors_are_statically_read_only() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline audit module"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for violation in _mutation_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        ):
            violations.append(f"{relative}:{violation}")

    assert violations == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("open(path, 'w').write('x')", "write_file_mode:w"),
        ("path.open(mode='a')", "write_file_mode:a"),
        ("path.open(mode=mode)", "dynamic_file_mode"),
        ("import os\nos.open(path, os.O_WRONLY | os.O_CREAT)", "write_os_flag"),
        ("path.write_text('replacement')", "mutating_method:write_text"),
        ("path.unlink()", "mutating_method:unlink"),
        ("import os\nos.replace(source, target)", "mutating_call:os.replace"),
        ("import shutil\nshutil.copyfile(source, target)", "mutating_call:shutil.copyfile"),
        ("handle.write(b'x')", "direct_write_method:write"),
        ("import json\njson.dump(value, handle)", "mutating_call:json.dump"),
        ("import numpy as np\nnp.save(path, value)", "mutating_call:numpy.save"),
        ('connection.execute("DELETE FROM evidence")', "mutating_sql:DELETE"),
        ('connection.executescript("CREATE TABLE repaired(id INTEGER)")', "mutating_sql:CREATE"),
        ('connection.execute("PRAGMA journal_mode=WAL")', "mutating_sql:PRAGMA"),
    ],
)
def test_contract_rejects_representative_mutation_surfaces(
    source: str,
    reason: str,
) -> None:
    violations = _mutation_violations(source)
    assert any(reason in violation for violation in violations), violations


def test_contract_allows_representative_read_only_audit_io() -> None:
    source = """
import json
import os
from pathlib import Path

flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(Path("evidence.json"), flags)
with os.fdopen(fd, "rb", closefd=True) as handle:
    payload = json.loads(handle.read())
with Path("manifest.json").open("r", encoding="utf-8") as handle:
    manifest = json.load(handle)
rows = connection.execute("SELECT sha256 FROM evidence").fetchall()
quick_check = connection.execute("PRAGMA quick_check").fetchone()
connection.execute("PRAGMA query_only=ON")
"""
    assert _mutation_violations(source) == []
