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

_CLOCK_CALLS = {
    "time.time",
    "time.time_ns",
    "time.monotonic",
    "time.monotonic_ns",
    "time.perf_counter",
    "time.perf_counter_ns",
    "time.process_time",
    "time.process_time_ns",
    "time.thread_time",
    "time.thread_time_ns",
}
_DATETIME_CLOCK_CALLS = {
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
}
_UUID_ENTROPY_CALLS = {
    "uuid.uuid1",
    "uuid.uuid4",
    "uuid.uuid6",
    "uuid.uuid7",
    "uuid.uuid8",
}
_WILDCARD_SENSITIVE_MODULES = {
    "builtins",
    "datetime",
    "os",
    "random",
    "secrets",
    "time",
    "uuid",
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


def _ambient_reason(name: str | None) -> str | None:
    if name is None:
        return None
    if name in {"hash", "builtins.hash"}:
        return "process_randomized_hash"
    if name == "os.environ" or name.startswith("os.environ."):
        return "environment"
    if name == "os.getenv":
        return "environment"
    if name == "os.urandom":
        return "os_entropy"
    if name in _CLOCK_CALLS:
        return "ambient_clock"
    if name in _DATETIME_CLOCK_CALLS:
        return "ambient_clock"
    if name in _UUID_ENTROPY_CALLS:
        return "uuid_entropy"
    if name == "random" or name.startswith("random."):
        return "random_entropy"
    if name == "secrets" or name.startswith("secrets."):
        return "secrets_entropy"
    return None


class _AmbientNondeterminismVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self.aliases[local] = target
            if item.name in {"random", "secrets"}:
                self.violations.append(
                    f"{node.lineno}:forbidden_entropy_import:{item.name}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for item in node.names:
            if item.name == "*" and module in _WILDCARD_SENSITIVE_MODULES:
                self.violations.append(
                    f"{node.lineno}:wildcard_sensitive_import:{module}"
                )
                continue
            local = item.asname or item.name
            target = f"{module}.{item.name}" if module else item.name
            self.aliases[local] = target
            if module in {"random", "secrets"}:
                self.violations.append(
                    f"{node.lineno}:forbidden_entropy_import:{module}"
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            reason = _ambient_reason(_qualname(node, self.aliases))
            if reason is not None:
                self.violations.append(f"{node.lineno}:{reason}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            reason = _ambient_reason(_qualname(node, self.aliases))
            if reason is not None:
                self.violations.append(f"{node.lineno}:{reason}")
        self.generic_visit(node)


def _ambient_violations(source: str, *, filename: str = "<contract>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = _AmbientNondeterminismVisitor()
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


def test_data_pipeline_auditors_have_no_ambient_nondeterminism() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline audit module"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for violation in _ambient_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        ):
            violations.append(f"{relative}:{violation}")

    assert violations == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("value = hash('evidence')", "process_randomized_hash"),
        ("from builtins import hash as h\nvalue = h('evidence')", "process_randomized_hash"),
        ("import os\nvalue = os.getenv('HAXLAB_MODE')", "environment"),
        ("from os import environ as env\nvalue = env['HAXLAB_MODE']", "environment"),
        ("import os\nvalue = os.urandom(16)", "os_entropy"),
        ("import time\nvalue = time.time()", "ambient_clock"),
        ("from time import monotonic as clock\nvalue = clock()", "ambient_clock"),
        ("from datetime import datetime\nvalue = datetime.now()", "ambient_clock"),
        ("import random\nvalue = random.random()", "random_entropy"),
        ("from random import Random\nvalue = Random()", "random_entropy"),
        ("import secrets\nvalue = secrets.token_hex()", "secrets_entropy"),
        ("import uuid\nvalue = uuid.uuid4()", "uuid_entropy"),
    ],
)
def test_contract_rejects_representative_ambient_sources(
    source: str,
    reason: str,
) -> None:
    violations = _ambient_violations(source)
    assert any(reason in violation for violation in violations), violations


def test_contract_allows_explicit_deterministic_parsing_and_hashing() -> None:
    source = """
from datetime import datetime
import hashlib
import uuid

timestamp = datetime.fromisoformat("2026-10-07T00:00:00+00:00")
digest = hashlib.sha256(b"explicit evidence").hexdigest()
stable_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
ordered = sorted((digest, timestamp.isoformat(), str(stable_id)))
"""
    assert _ambient_violations(source) == []
