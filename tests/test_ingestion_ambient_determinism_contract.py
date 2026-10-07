from __future__ import annotations

import ast
from pathlib import Path

import pytest


INGESTION_ROOT = Path(__file__).resolve().parents[1] / "src" / "haxlab" / "ingestion"

FORBIDDEN_CALLS = {
    "datetime.date.today",
    "datetime.datetime.now",
    "datetime.datetime.today",
    "datetime.datetime.utcnow",
    "time.clock_gettime",
    "time.monotonic",
    "time.monotonic_ns",
    "time.perf_counter",
    "time.perf_counter_ns",
    "time.process_time",
    "time.process_time_ns",
    "time.thread_time",
    "time.thread_time_ns",
    "time.time",
    "time.time_ns",
    "uuid.uuid1",
    "uuid.uuid4",
}

FORBIDDEN_PREFIXES = (
    "random.",
    "secrets.",
)


def _name(expr: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id, expr.id)
    if isinstance(expr, ast.Attribute):
        base = _name(expr.value, aliases)
        return f"{base}.{expr.attr}" if base else None
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "getattr"
        and len(expr.args) >= 2
        and isinstance(expr.args[1], ast.Constant)
        and isinstance(expr.args[1].value, str)
    ):
        base = _name(expr.args[0], aliases)
        return f"{base}.{expr.args[1].value}" if base else None
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                if item.name == "*":
                    continue
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            resolved = _name(node.value, aliases)
            if resolved and aliases.get(target.id) != resolved:
                aliases[target.id] = resolved
                changed = True

    return aliases


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _aliases(tree)
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _name(node.func, aliases)
        if resolved is None:
            continue
        if resolved in FORBIDDEN_CALLS or resolved.startswith(FORBIDDEN_PREFIXES):
            violations.append(f"{node.lineno}:{resolved}")

    return sorted(violations)


def test_ingestion_package_has_no_ambient_nondeterminism() -> None:
    violations: dict[str, list[str]] = {}

    for path in sorted(INGESTION_ROOT.rglob("*.py")):
        found = _violations(path.read_text(encoding="utf-8"))
        if found:
            violations[str(path.relative_to(INGESTION_ROOT.parent.parent.parent))] = found

    assert violations == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import random\nrandom.random()\n", "random.random"),
        ("import random as r\nr.choice([1, 2])\n", "random.choice"),
        ("from secrets import token_hex as token\ntoken(8)\n", "secrets.token_hex"),
        ("import uuid\nf = uuid.uuid4\nf()\n", "uuid.uuid4"),
        ("import time as t\ngetattr(t, 'time')()\n", "time.time"),
        (
            "from datetime import datetime as dt\nclock = getattr(dt, 'now')\nclock()\n",
            "datetime.datetime.now",
        ),
    ],
)
def test_contract_rejects_alias_hidden_ambient_sources(
    source: str,
    expected: str,
) -> None:
    assert any(expected in violation for violation in _violations(source))


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\ndatetime.fromisoformat('2026-10-07T00:00:00')\n",
        "import time\ntime.sleep(0)\n",
        "import uuid\nuuid.uuid5(uuid.NAMESPACE_DNS, 'haxlab')\n",
        "value = 'explicit-input-timestamp'\n",
    ],
)
def test_contract_preserves_explicit_or_deterministic_operations(source: str) -> None:
    assert _violations(source) == []
