from __future__ import annotations

import ast
from pathlib import Path


EVALUATION_ROOT = Path("src/haxlab/evaluation")

BANNED_IMPORTS = (
    "subprocess",
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "urllib.request",
    "http.client",
)

BANNED_CALLS = {
    "os.system",
    "os.popen",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}


def _is_banned_import(name: str) -> bool:
    return any(name == banned or name.startswith(f"{banned}.") for banned in BANNED_IMPORTS)


def _qualified_name(
    node: ast.AST,
    module_aliases: dict[str, str],
    symbol_aliases: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Name):
        return symbol_aliases.get(node.id, module_aliases.get(node.id, node.id))
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, module_aliases, symbol_aliases)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def test_evaluation_product_code_is_network_and_subprocess_hermetic() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_aliases: dict[str, str] = {}
        symbol_aliases: dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_banned_import(alias.name):
                        violations.append(f"{path}:{node.lineno}:import:{alias.name}")
                    module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    qualified = f"{node.module}.{alias.name}"
                    if _is_banned_import(node.module) or _is_banned_import(qualified):
                        violations.append(f"{path}:{node.lineno}:import:{qualified}")
                    if alias.name != "*":
                        symbol_aliases[alias.asname or alias.name] = qualified

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _qualified_name(node.func, module_aliases, symbol_aliases)
            if name in BANNED_CALLS:
                violations.append(f"{path}:{node.lineno}:call:{name}")

    assert not violations, (
        "evaluation product code must stay hermetic from network state and "
        "child-process/shell execution; violations: "
        + ", ".join(sorted(set(violations)))
    )
