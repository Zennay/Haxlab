from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_ATEXIT_CALLS = {
    "atexit._clear",
    "atexit._run_exitfuncs",
    "atexit.register",
    "atexit.unregister",
}

TRACKED_MODULES = {"atexit", "builtins"}


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


def _name_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_name_targets(element))
        return names
    return []


def _aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in TRACKED_MODULES:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]

            if value is None:
                continue

            resolved = _canonical_name(value, aliases)
            if resolved not in FORBIDDEN_ATEXIT_CALLS:
                continue

            for target in targets:
                for local in _name_targets(target):
                    if aliases.get(local) != resolved:
                        aliases[local] = resolved
                        changed = True

    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_ATEXIT_CALLS:
                findings.append(
                    f"line {node.lineno}: process-exit callback registry mutation: {target}"
                )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = _canonical_name(decorator, aliases)
                if target == "atexit.register":
                    findings.append(
                        f"line {decorator.lineno}: process-exit callback decorator: {target}"
                    )

    return sorted(set(findings))


def test_evaluation_package_does_not_mutate_atexit_registry() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        violations.extend(f"{relative}: {finding}" for finding in findings)

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import atexit\natexit.register(lambda: None)\n", "atexit.register"),
        ("import atexit as ax\nax.unregister(lambda: None)\n", "atexit.unregister"),
        ("from atexit import register as on_exit\non_exit(lambda: None)\n", "atexit.register"),
        (
            "import atexit\nregister = atexit.register\nregister(lambda: None)\n",
            "atexit.register",
        ),
        (
            "import atexit\nregister: object = getattr(atexit, 'register')\nregister(lambda: None)\n",
            "atexit.register",
        ),
        (
            "import atexit\n(getattr(atexit, '_clear'))()\n",
            "atexit._clear",
        ),
        (
            "from atexit import _run_exitfuncs as run\nrun()\n",
            "atexit._run_exitfuncs",
        ),
        (
            "import atexit\n@atexit.register\ndef cleanup():\n    pass\n",
            "atexit.register",
        ),
        (
            "from atexit import register as on_exit\n@on_exit\nasync def cleanup():\n    pass\n",
            "atexit.register",
        ),
    ],
)
def test_detector_rejects_atexit_registry_mutation(source: str, expected: str) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "def probe():\n    return 'ok'\n",
        "callbacks = ('finalize', 'report')\n",
        "class LocalRegistry:\n    def register(self, callback):\n        return callback\nLocalRegistry().register(lambda: None)\n",
    ],
)
def test_detector_preserves_non_atexit_usage(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
