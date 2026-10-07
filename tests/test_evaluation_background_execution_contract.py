from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "_thread.start_new_thread",
    "asyncio.TaskGroup",
    "asyncio.create_task",
    "asyncio.ensure_future",
    "asyncio.run_coroutine_threadsafe",
    "asyncio.to_thread",
    "asyncio.get_event_loop().create_task",
    "asyncio.get_event_loop().run_in_executor",
    "asyncio.get_running_loop().create_task",
    "asyncio.get_running_loop().run_in_executor",
    "concurrent.futures.ProcessPoolExecutor",
    "concurrent.futures.ThreadPoolExecutor",
    "multiprocessing.Process",
    "multiprocessing.Pool",
    "multiprocessing.context.Process",
    "multiprocessing.context.Pool",
    "multiprocessing.get_context().Process",
    "multiprocessing.get_context().Pool",
    "multiprocessing.dummy.Pool",
    "multiprocessing.pool.Pool",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.posix_spawnp",
    "threading.Thread",
    "threading.Timer",
}

TRACKED_MODULE_PREFIXES = (
    "_thread",
    "asyncio",
    "concurrent",
    "multiprocessing",
    "os",
    "threading",
)


def _evaluation_modules() -> list[Path]:
    return sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())


def _tracked_module(module: str) -> bool:
    return module == "_thread" or any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in TRACKED_MODULE_PREFIXES
        if prefix != "_thread"
    )


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
        if accessor in {"asyncio.get_event_loop", "asyncio.get_running_loop", "multiprocessing.get_context"}:
            return f"{accessor}()"
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _tracked_module(alias.name):
                    aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and _tracked_module(node.module):
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
                target = _canonical_name(node.value, aliases)
                if target in FORBIDDEN_CALLS:
                    local = node.targets[0].id
                    if aliases.get(local) != target:
                        aliases[local] = target
                        changed = True
    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _canonical_name(node.func, aliases)
        if target in FORBIDDEN_CALLS:
            findings.append(
                f"line {node.lineno}: hidden background/concurrent execution: {target}"
            )

    return sorted(set(findings))


def test_evaluation_package_has_no_hidden_background_execution() -> None:
    modules = _evaluation_modules()
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


def test_contract_rejects_thread_process_and_executor_construction() -> None:
    source = textwrap.dedent(
        """
        import _thread
        import os
        import threading as threads
        import multiprocessing as mp
        import concurrent.futures as futures
        from concurrent.futures import ProcessPoolExecutor as ProcessPool

        def probe():
            threads.Thread(target=lambda: None)
            threads.Timer(1.0, lambda: None)
            mp.Process(target=lambda: None)
            mp.Pool(2)
            mp.get_context("spawn").Process(target=lambda: None)
            futures.ThreadPoolExecutor(max_workers=2)
            ProcessPool(max_workers=2)
            _thread.start_new_thread(lambda: None, ())
            os.fork()
            os.posix_spawn("/bin/true", ["/bin/true"], {})
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "threading.Thread",
        "threading.Timer",
        "multiprocessing.Process",
        "multiprocessing.Pool",
        "multiprocessing.get_context().Process",
        "concurrent.futures.ThreadPoolExecutor",
        "concurrent.futures.ProcessPoolExecutor",
        "_thread.start_new_thread",
        "os.fork",
        "os.posix_spawn",
    ):
        assert expected in findings


def test_contract_rejects_detached_asyncio_scheduling() -> None:
    source = textwrap.dedent(
        """
        import asyncio as aio
        from asyncio import ensure_future as detach

        async def work():
            return 1

        async def probe():
            aio.create_task(work())
            detach(work())
            aio.to_thread(lambda: None)
            aio.run_coroutine_threadsafe(work(), aio.get_running_loop())
            aio.get_running_loop().create_task(work())
            aio.get_event_loop().run_in_executor(None, lambda: None)
            aio.TaskGroup()
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "asyncio.create_task",
        "asyncio.ensure_future",
        "asyncio.to_thread",
        "asyncio.run_coroutine_threadsafe",
        "asyncio.get_running_loop().create_task",
        "asyncio.get_event_loop().run_in_executor",
        "asyncio.TaskGroup",
    ):
        assert expected in findings


def test_contract_resolves_assignment_and_constant_getattr_aliases() -> None:
    source = textwrap.dedent(
        """
        import asyncio
        import threading
        from builtins import getattr as read_attr

        spawn = asyncio.create_task
        thread_factory = read_attr(threading, "Thread")

        async def probe(coro):
            spawn(coro)
            thread_factory(target=lambda: None)
            getattr(asyncio, "ensure_future")(coro)
        """
    )

    findings = "\n".join(scan_source(source))
    assert "asyncio.create_task" in findings
    assert "threading.Thread" in findings
    assert "asyncio.ensure_future" in findings


def test_contract_allows_synchronous_and_unscheduled_async_code() -> None:
    source = textwrap.dedent(
        """
        import asyncio
        import threading

        async def calculate(value):
            await asyncio.sleep(0)
            return value + 1

        def probe():
            lock = threading.Lock()
            with lock:
                return asyncio.run(calculate(1))
        """
    )

    assert scan_source(source) == []
