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
AUDIT_ROOTS = tuple(
    SRC / "haxlab" / package
    for package in ("ingestion", "learning", "runtime")
)

FORBIDDEN_IMPORTS = {
    "http.client",
    "requests",
    "socket",
    "subprocess",
    "urllib.request",
}
FORBIDDEN_CALLS = {
    "os.popen",
    "os.system",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "builtins.__import__",
    "importlib.import_module",
    "__import__",
}
FORBIDDEN_CALL_PREFIXES = (
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
)


def _audit_modules() -> list[Path]:
    modules: list[Path] = []
    for root in AUDIT_ROOTS:
        if root.exists():
            modules.extend(sorted(root.rglob("*_audit.py")))
    return sorted(set(modules))


def _is_forbidden_import(name: str) -> bool:
    return any(
        name == forbidden or name.startswith(f"{forbidden}.")
        for forbidden in FORBIDDEN_IMPORTS
    )


def _is_forbidden_call(name: str) -> bool:
    return name in FORBIDDEN_CALLS or any(
        name.startswith(prefix)
        for prefix in FORBIDDEN_CALL_PREFIXES
    )


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _forbidden_effects(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                aliases[bound] = alias.name
                if _is_forbidden_import(alias.name):
                    violations.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden_import(module):
                violations.append(f"import:{module}")
            for alias in node.names:
                if alias.name == "*":
                    continue
                full_name = f"{module}.{alias.name}" if module else alias.name
                bound = alias.asname or alias.name
                aliases[bound] = full_name
                if _is_forbidden_import(full_name):
                    violations.append(f"import:{full_name}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name is None and isinstance(node.func, ast.Name):
            name = aliases.get(node.func.id, node.func.id)
        elif name:
            head, dot, tail = name.partition(".")
            if head in aliases:
                name = aliases[head] + (dot + tail if dot else "")
        if name is not None and _is_forbidden_call(name):
            violations.append(f"call:{name}")

    return sorted(set(violations))


def test_data_pipeline_audit_modules_are_static_hermetic_verifiers() -> None:
    modules = _audit_modules()
    assert modules, "expected at least one data-pipeline *_audit.py module"

    violations: dict[str, list[str]] = {}
    for path in modules:
        found = _forbidden_effects(path.read_text(encoding="utf-8"))
        if found:
            violations[str(path.relative_to(ROOT))] = found

    assert violations == {}


def test_static_checker_rejects_network_and_process_execution() -> None:
    source = textwrap.dedent(
        """
        import socket as net
        from subprocess import run as execute
        from urllib import request
        from http import client as http_client
        from importlib import import_module
        import os

        net.socket()
        execute(["echo", "unexpected"])
        request.urlopen("https://example.invalid")
        http_client.HTTPSConnection("example.invalid")
        import_module("socket")
        __import__("subprocess")
        os.spawnlp(os.P_WAIT, "echo", "echo", "unexpected")
        os.system("true")
        """
    )

    assert _forbidden_effects(source) == [
        "call:__import__",
        "call:importlib.import_module",
        "call:os.spawnlp",
        "call:os.system",
        "call:subprocess.run",
        "import:http.client",
        "import:socket",
        "import:subprocess",
        "import:subprocess.run",
        "import:urllib.request",
    ]


def test_data_pipeline_audit_modules_import_without_side_effects(tmp_path: Path) -> None:
    modules = _audit_modules()
    assert modules

    import_names = [
        ".".join(path.relative_to(SRC).with_suffix("").parts)
        for path in modules
    ]
    script = textwrap.dedent(
        """
        import importlib
        import json
        import os
        import sys

        modules = json.loads(sys.argv[1])
        sys.path.insert(0, sys.argv[2])

        def audit_hook(event, args):
            if event == "open":
                if len(args) > 1:
                    mode = args[1]
                    if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")):
                        raise RuntimeError(f"import-time write blocked: {args[0]!r} mode={mode!r}")
                    if isinstance(mode, int):
                        write_flags = (
                            getattr(os, "O_WRONLY", 0)
                            | getattr(os, "O_RDWR", 0)
                            | getattr(os, "O_CREAT", 0)
                            | getattr(os, "O_TRUNC", 0)
                            | getattr(os, "O_APPEND", 0)
                        )
                        if mode & write_flags:
                            raise RuntimeError(f"import-time fd write blocked: {args[0]!r} flags={mode!r}")
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
                raise RuntimeError(f"import-time filesystem mutation blocked: {event}")
            if event.startswith("subprocess.") or event.startswith("socket."):
                raise RuntimeError(f"import-time external side effect blocked: {event}")

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
            json.dumps(import_names),
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
