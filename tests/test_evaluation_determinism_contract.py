from __future__ import annotations

import ast
from pathlib import Path


EVALUATION_ROOT = Path("src/haxlab/evaluation")

BANNED_CALLS = {
    "builtins.hash",
    "os.getenv",
    "time.time",
    "time.time_ns",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.date.today",
    "uuid.uuid1",
    "uuid.uuid4",
}

BANNED_PREFIXES = (
    "os.environ",
    "secrets.",
)

BANNED_RANDOM_CALLS = {
    "random.seed",
    "random.random",
    "random.randint",
    "random.randrange",
    "random.choice",
    "random.choices",
    "random.shuffle",
    "random.sample",
    "random.uniform",
    "random.triangular",
    "random.betavariate",
    "random.expovariate",
    "random.gammavariate",
    "random.gauss",
    "random.lognormvariate",
    "random.normalvariate",
    "random.vonmisesvariate",
    "random.paretovariate",
    "random.weibullvariate",
    "random.getrandbits",
    "random.randbytes",
}


def _qualified_name(
    node: ast.AST,
    module_aliases: dict[str, str],
    symbol_aliases: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id == "hash":
            return "builtins.hash"
        return symbol_aliases.get(node.id, module_aliases.get(node.id, node.id))
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, module_aliases, symbol_aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


def test_evaluation_product_code_has_no_ambient_nondeterminism() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_aliases: dict[str, str] = {}
        symbol_aliases: dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if alias.name == "*":
                        violations.append(f"{path}:{node.lineno}:wildcard-import")
                        continue
                    symbol_aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                name = _qualified_name(node, module_aliases, symbol_aliases)
                if name and name.startswith(BANNED_PREFIXES):
                    violations.append(f"{path}:{node.lineno}:{name}")
            elif isinstance(node, ast.Call):
                name = _qualified_name(node.func, module_aliases, symbol_aliases)
                if name in BANNED_CALLS or name in BANNED_RANDOM_CALLS:
                    violations.append(f"{path}:{node.lineno}:{name}")
                elif name == "random.Random" and not node.args and not node.keywords:
                    violations.append(f"{path}:{node.lineno}:random.Random(no-seed)")
                elif name and name.startswith("secrets."):
                    violations.append(f"{path}:{node.lineno}:{name}")

    assert not violations, (
        "evaluation product code must remain deterministic for identical "
        "explicit inputs; ambient nondeterminism detected: "
        + ", ".join(sorted(set(violations)))
    )
