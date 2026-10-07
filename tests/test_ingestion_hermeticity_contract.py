from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
INGESTION_ROOT = SRC / "haxlab" / "ingestion"

FORBIDDEN_IMPORTS = {
    "aiohttp",
    "asyncio.subprocess",
    "ftplib",
    "http.client",
    "httpx",
    "multiprocessing",
    "pexpect",
    "requests",
    "socket",
    "subprocess",
    "urllib.request",
}
FORBIDDEN_CALLS = {
    "__import__",
    "builtins.__import__",
    "importlib.import_module",
    "os.popen",
    "os.system",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}
FORBIDDEN_CALL_PREFIXES = (
    "os.exec",
    "os.fork",
    "os.posix_spawn",
    "os.spawn",
)


def _ingestion_modules() -> list[Path]:
    return sorted(path for path in INGESTION_ROOT.rglob("*.py") if path.is_file())


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_forbidden_import(name: str) -> bool:
    return any(
        name == forbidden or name.startswith(f"{forbidden}.")
        for forbidden in FORBIDDEN_IMPORTS
    )


def _resolve_expr(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolve_expr(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        parent = _resolve_expr(node.args[0], aliases)
        return f"{parent}.{node.args[1].value}" if parent else None
    return None


def _collect_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {"__import__": "builtins.__import__"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                target = f"{module}.{alias.name}" if module else alias.name
                aliases[alias.asname or alias.name] = target

    assignments = [
        node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            value = node.value
            if value is None:
                continue
            resolved = _resolve_expr(value, aliases)
            if resolved is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != resolved:
                    aliases[target.id] = resolved
                    changed = True
        if not changed:
            break

    return aliases


def _is_forbidden_call(name: str) -> bool:
    return name in FORBIDDEN_CALLS or any(
        name.startswith(prefix) for prefix in FORBIDDEN_CALL_PREFIXES
    )


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _collect_aliases(tree)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_import(alias.name):
                    found.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden_import(module):
                found.append(f"import:{module}")
            for alias in node.names:
                if alias.name == "*":
                    continue
                target = f"{module}.{alias.name}" if module else alias.name
                if _is_forbidden_import(target):
                    found.append(f"import:{target}")
        elif isinstance(node, ast.Call):
            resolved = _resolve_expr(node.func, aliases)
            if resolved is not None and _is_forbidden_call(resolved):
                found.append(f"call:{resolved}")

    return sorted(set(found))


def test_ingestion_modules_are_offline_and_process_hermetic() -> None:
    modules = _ingestion_modules()
    assert modules, "expected ingestion Python modules"

    violations: dict[str, list[str]] = {}
    for path in modules:
        found = _violations(path.read_text(encoding="utf-8"))
        if found:
            violations[str(path.relative_to(ROOT))] = found

    assert violations == {}


def test_contract_rejects_network_process_and_dynamic_import_surfaces() -> None:
    source = textwrap.dedent(
        """
        import socket as net
        import subprocess as sp
        from urllib import request as request_api
        import importlib
        import multiprocessing as mp
        import os

        launch = sp.run
        imported = getattr(importlib, "import_module")

        net.socket()
        mp.Process(target=lambda: None)
        launch(["echo", "unexpected"])
        request_api.urlopen("https://example.invalid")
        imported("socket")
        __import__("subprocess")
        os.spawnlp(os.P_WAIT, "echo", "echo", "unexpected")
        """
    )

    assert _violations(source) == [
        "call:builtins.__import__",
        "call:importlib.import_module",
        "call:os.spawnlp",
        "call:subprocess.run",
        "import:multiprocessing",
        "import:socket",
        "import:subprocess",
        "import:urllib.request",
    ]


def test_contract_allows_local_deterministic_ingestion_primitives() -> None:
    source = textwrap.dedent(
        """
        import hashlib
        import json
        import os
        from pathlib import Path

        def load(path: Path) -> str:
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            return json.dumps(
                {"sha256": digest, "size": len(payload), "path": os.fspath(path)}
            )
        """
    )

    assert _violations(source) == []


def test_ingestion_modules_import_without_external_side_effects(tmp_path: Path) -> None:
    modules = sorted({_module_name(path) for path in _ingestion_modules()})
    assert modules

    script = textwrap.dedent(
        """
        import importlib
        import json
        import os
        import sys

        modules = json.loads(sys.argv[1])
        sys.path.insert(0, sys.argv[2])

        def audit_hook(event, args):
            if event == "open" and len(args) > 1:
                mode = args[1]
                if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")):
                    raise RuntimeError(
                        f"ingestion import-time write blocked: {args[0]!r} mode={mode!r}"
                    )
                if isinstance(mode, int):
                    write_flags = (
                        getattr(os, "O_WRONLY", 0)
                        | getattr(os, "O_RDWR", 0)
                        | getattr(os, "O_CREAT", 0)
                        | getattr(os, "O_TRUNC", 0)
                        | getattr(os, "O_APPEND", 0)
                    )
                    if mode & write_flags:
                        raise RuntimeError(
                            f"ingestion import-time fd write blocked: {args[0]!r}"
                        )
            if event in {
                "os.chdir",
                "os.chmod",
                "os.chown",
                "os.link",
                "os.mkdir",
                "os.remove",
                "os.rename",
                "os.rmdir",
                "os.symlink",
                "os.truncate",
                "os.utime",
                "os.setxattr",
                "os.removexattr",
            }:
                raise RuntimeError(
                    f"ingestion import-time filesystem mutation blocked: {event}"
                )
            if event.startswith("subprocess.") or event.startswith("socket."):
                raise RuntimeError(
                    f"ingestion import-time external side effect blocked: {event}"
                )

        sys.addaudithook(audit_hook)
        for module in modules:
            importlib.import_module(module)
        """
    )

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            json.dumps(modules),
            str(SRC),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
