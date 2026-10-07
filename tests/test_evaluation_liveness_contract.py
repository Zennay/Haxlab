from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

DIRECT_BLOCKING_CALLS = {
    "asyncio.sleep",
    "builtins.input",
    "input",
    "signal.pause",
    "time.sleep",
}

BLOCKING_METHODS = {
    "concurrent.futures.Future": {"exception", "result"},
    "multiprocessing.Event": {"wait"},
    "multiprocessing.Queue": {"get", "join"},
    "multiprocessing.SimpleQueue": {"get"},
    "queue.Queue": {"get", "join"},
    "queue.SimpleQueue": {"get"},
    "threading.Barrier": {"wait"},
    "threading.BoundedSemaphore": {"acquire"},
    "threading.Condition": {"wait", "wait_for"},
    "threading.Event": {"wait"},
    "threading.Lock": {"acquire"},
    "threading.RLock": {"acquire"},
    "threading.Semaphore": {"acquire"},
}

TRACKED_MODULES = {
    "asyncio",
    "builtins",
    "concurrent",
    "concurrent.futures",
    "multiprocessing",
    "queue",
    "signal",
    "threading",
    "time",
}


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
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


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in TRACKED_MODULES:
                    aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in TRACKED_MODULES:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                value = _canonical_name(node.value, aliases)
                if value in DIRECT_BLOCKING_CALLS or value in BLOCKING_METHODS:
                    local = node.targets[0].id
                    if aliases.get(local) != value:
                        aliases[local] = value
                        changed = True
    return aliases


def _object_type(
    node: ast.AST | None,
    aliases: dict[str, str],
    objects: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return objects.get(node.id)
    if isinstance(node, ast.Call):
        target = _canonical_name(node.func, aliases)
        if target in BLOCKING_METHODS:
            return target
    return None


def _objects(tree: ast.AST, aliases: dict[str, str]) -> dict[str, str]:
    objects: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                value_type = _object_type(node.value, aliases, objects)
                if value_type and objects.get(node.targets[0].id) != value_type:
                    objects[node.targets[0].id] = value_type
                    changed = True
    return objects


def _callable_name(
    node: ast.AST | None,
    aliases: dict[str, str],
    objects: dict[str, str],
    callable_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in callable_aliases:
        return callable_aliases[node.id]

    direct = _canonical_name(node, aliases)
    if direct in DIRECT_BLOCKING_CALLS:
        return direct

    if isinstance(node, ast.Attribute):
        owner_type = _object_type(node.value, aliases, objects)
        if owner_type and node.attr in BLOCKING_METHODS.get(owner_type, set()):
            return f"{owner_type}.{node.attr}"

    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner_type = _object_type(node.args[0], aliases, objects)
            method = node.args[1].value
            if owner_type and method in BLOCKING_METHODS.get(owner_type, set()):
                return f"{owner_type}.{method}"

    return None


def _callable_aliases(
    tree: ast.AST,
    aliases: dict[str, str],
    objects: dict[str, str],
) -> dict[str, str]:
    callable_aliases: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                value = _callable_name(
                    node.value,
                    aliases,
                    objects,
                    callable_aliases,
                )
                if value and callable_aliases.get(node.targets[0].id) != value:
                    callable_aliases[node.targets[0].id] = value
                    changed = True
    return callable_aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    objects = _objects(tree, aliases)
    callable_aliases = _callable_aliases(tree, aliases, objects)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _callable_name(node.func, aliases, objects, callable_aliases)
        if target in DIRECT_BLOCKING_CALLS or (
            target
            and "." in target
            and target.rsplit(".", 1)[0] in BLOCKING_METHODS
        ):
            findings.append(f"line {node.lineno}: blocking/interactive call: {target}")

    return sorted(set(findings))


def test_evaluation_library_has_no_blocking_or_interactive_calls() -> None:
    modules = sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def gate():\n    input('continue?')\n", "input"),
        ("import time\ndef gate():\n    time.sleep(1)\n", "time.sleep"),
        ("from time import sleep as nap\ndef gate():\n    nap(1)\n", "time.sleep"),
        (
            "import time\nnap = getattr(time, 'sleep')\ndef gate():\n    nap(1)\n",
            "time.sleep",
        ),
        (
            "import asyncio\nasync def gate():\n    await asyncio.sleep(1)\n",
            "asyncio.sleep",
        ),
        ("import signal\ndef gate():\n    signal.pause()\n", "signal.pause"),
        (
            "import threading\ndef gate():\n    event = threading.Event()\n    event.wait()\n",
            "threading.Event.wait",
        ),
        (
            "from threading import Condition as C\ndef gate():\n"
            "    condition = C()\n    wait = condition.wait\n    wait()\n",
            "threading.Condition.wait",
        ),
        (
            "import concurrent.futures as cf\ndef gate():\n"
            "    future = cf.Future()\n    future.result()\n",
            "concurrent.futures.Future.result",
        ),
        (
            "from queue import Queue\ndef gate():\n    q = Queue()\n    q.get()\n",
            "queue.Queue.get",
        ),
        (
            "import multiprocessing as mp\ndef gate():\n"
            "    event = mp.Event()\n    getattr(event, 'wait')()\n",
            "multiprocessing.Event.wait",
        ),
    ],
)
def test_contract_rejects_blocking_and_interactive_paths(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_preserves_nonblocking_get_and_local_computation() -> None:
    source = """
def gate(payload: dict[str, object]) -> object | None:
    value = payload.get("score")
    return value
"""
    assert scan_source(source) == []


def test_contract_does_not_guess_custom_wait_methods() -> None:
    source = """
class DeterministicAccumulator:
    def wait(self) -> int:
        return 1

def gate() -> int:
    return DeterministicAccumulator().wait()
"""
    assert scan_source(source) == []
