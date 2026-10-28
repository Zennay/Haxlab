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
ENV_OBJECTS = {"os.environ", "os.environb"}
ENV_FUNCTIONS = {"os.putenv", "os.unsetenv"}
MUTATING_METHODS = {
    "__delitem__",
    "__ior__",
    "__setitem__",
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
}


def _audit_paths() -> list[Path]:
    return sorted(
        {
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        },
        key=lambda path: path.as_posix(),
    )


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _canonical_name(node.value, aliases)
        return f"{owner}.{node.attr}" if owner else node.attr
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


def _simple_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return node.target.id, node.value
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None


def _is_env_mutator(name: str | None) -> bool:
    if not name:
        return False
    if name in ENV_FUNCTIONS:
        return True
    for owner in ENV_OBJECTS:
        if name.startswith(owner + ".") and name.rsplit(".", 1)[-1] in MUTATING_METHODS:
            return True
    return False


def _tracked_name(name: str | None) -> bool:
    return bool(name and (name in ENV_OBJECTS or _is_env_mutator(name)))


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "os":
                    aliases[item.asname or "os"] = "os"
                elif item.name == "builtins":
                    aliases[item.asname or "builtins"] = "builtins"
                elif item.name == "operator":
                    aliases[item.asname or "operator"] = "operator"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in {"builtins", "operator", "os"}:
                continue
            for item in node.names:
                if item.name == "*":
                    continue
                aliases[item.asname or item.name] = f"{module}.{item.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            target = _canonical_name(expression, aliases)
            if _tracked_name(target) and aliases.get(local) != target:
                aliases[local] = target
                changed = True

    return aliases


def _environment_owner(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    name = _canonical_name(node, aliases)
    if name in ENV_OBJECTS:
        return name
    return None


def _target_mutates_environment(
    target: ast.AST,
    aliases: dict[str, str],
    *,
    allow_name_alias: bool,
) -> str | None:
    if isinstance(target, ast.Subscript):
        return _environment_owner(target.value, aliases)
    if isinstance(target, ast.Attribute):
        return _environment_owner(target, aliases)
    if allow_name_alias and isinstance(target, ast.Name):
        return _environment_owner(target, aliases)
    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if _is_env_mutator(target):
                findings.append(
                    f"line {node.lineno}: process environment mutation call: {target}"
                )
                continue
            if target in {"operator.delitem", "operator.ior", "operator.setitem"} and node.args:
                owner = _environment_owner(node.args[0], aliases)
                if owner:
                    findings.append(
                        f"line {node.lineno}: process environment functional mutation: {target}({owner}, ...)"
                    )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                owner = _target_mutates_environment(
                    target, aliases, allow_name_alias=False
                )
                if owner:
                    findings.append(
                        f"line {node.lineno}: process environment assignment: {owner}"
                    )
        elif isinstance(node, ast.AnnAssign):
            owner = _target_mutates_environment(
                node.target, aliases, allow_name_alias=False
            )
            if owner:
                findings.append(
                    f"line {node.lineno}: process environment assignment: {owner}"
                )
        elif isinstance(node, ast.AugAssign):
            owner = _target_mutates_environment(
                node.target, aliases, allow_name_alias=True
            )
            if owner:
                findings.append(
                    f"line {node.lineno}: process environment augmented assignment: {owner}"
                )
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                owner = _target_mutates_environment(
                    target, aliases, allow_name_alias=False
                )
                if owner:
                    findings.append(
                        f"line {node.lineno}: process environment deletion: {owner}"
                    )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_mutate_process_environment() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(path.read_text(encoding="utf-8"), filename=relative)
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import os\ndef audit():\n    os.environ['MODE'] = 'unsafe'\n",
            "assignment: os.environ",
        ),
        (
            "from os import environ as env\ndef audit():\n    del env['MODE']\n",
            "deletion: os.environ",
        ),
        (
            "import os\ndef audit():\n    os.environ = {'MODE': 'unsafe'}\n",
            "assignment: os.environ",
        ),
        (
            "import os\ndef audit():\n    del os.environ\n",
            "deletion: os.environ",
        ),
        (
            "import os\ndef audit():\n    os.environ |= {'MODE': 'unsafe'}\n",
            "augmented assignment: os.environ",
        ),
        (
            "import os\ndef audit():\n"
            "    env = os.environ\n"
            "    env |= {'MODE': 'unsafe'}\n",
            "augmented assignment: os.environ",
        ),
        (
            "import os\ndef audit():\n    os.environ.update({'MODE': 'unsafe'})\n",
            "os.environ.update",
        ),
        (
            "import os\ndef audit():\n    env = os.environ\n    env.pop('MODE', None)\n",
            "os.environ.pop",
        ),
        (
            "from builtins import getattr as read_attr\nimport os\n"
            "def audit():\n"
            "    mutate = read_attr(os.environ, 'setdefault')\n"
            "    mutate('MODE', 'unsafe')\n",
            "os.environ.setdefault",
        ),
        (
            "from os import putenv as publish\ndef audit():\n"
            "    publish('MODE', 'unsafe')\n",
            "os.putenv",
        ),
        (
            "import os\ndef audit():\n    os.unsetenv('MODE')\n",
            "os.unsetenv",
        ),
        (
            "import operator, os\ndef audit():\n"
            "    operator.setitem(os.environ, 'MODE', 'unsafe')\n",
            "operator.setitem",
        ),
        (
            "import operator, os\ndef audit():\n"
            "    operator.ior(os.environ, {'MODE': 'unsafe'})\n",
            "operator.ior",
        ),
        (
            "from os import environb as env\ndef audit():\n"
            "    env[b'MODE'] = b'unsafe'\n",
            "assignment: os.environb",
        ),
    ],
)
def test_contract_rejects_process_environment_mutation(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


@pytest.mark.parametrize(
    "source",
    [
        (
            "import os\ndef audit():\n"
            "    env = os.environ\n"
            "    return env.get('MODE')\n"
        ),
        (
            "import os\ndef audit():\n"
            "    return os.getenv('MODE'), os.environ.get('MODE')\n"
        ),
        (
            "from os import environ as env\ndef audit():\n"
            "    return env['MODE'] if 'MODE' in env else None\n"
        ),
        (
            "import os\ndef audit():\n"
            "    snapshot = os.environ.copy()\n"
            "    snapshot['MODE'] = 'local'\n"
            "    return snapshot\n"
        ),
        (
            "def audit():\n"
            "    local = {}\n"
            "    local.update({'MODE': 'local'})\n"
            "    return local\n"
        ),
    ],
)
def test_contract_allows_environment_reads_and_local_mutation(source: str) -> None:
    assert scan_source(source) == []
