from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

MODULE_MUTATIONS = {
    "os.chmod",
    "os.chown",
    "os.fchmod",
    "os.fchown",
    "os.ftruncate",
    "os.lchmod",
    "os.lchown",
    "os.link",
    "os.remove",
    "os.removedirs",
    "os.removexattr",
    "os.rename",
    "os.renames",
    "os.replace",
    "os.rmdir",
    "os.setxattr",
    "os.symlink",
    "os.truncate",
    "os.unlink",
    "os.utime",
    "shutil.chown",
    "shutil.move",
    "shutil.rmtree",
}

PATH_MUTATIONS = {
    "chmod",
    "hardlink_to",
    "lchmod",
    "rename",
    "replace",
    "rmdir",
    "symlink_to",
    "touch",
    "unlink",
}

TRACKED_MODULES = {"os", "pathlib", "shutil"}


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


def _annotation_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _annotation_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value, aliases)
    return None


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
    aliases: dict[str, str] = {"Path": "pathlib.Path"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in TRACKED_MODULES:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in TRACKED_MODULES:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            value = _canonical_name(expression, aliases)
            if value in MODULE_MUTATIONS or value == "pathlib.Path":
                if aliases.get(local) != value:
                    aliases[local] = value
                    changed = True
    return aliases


def _is_path_expression(
    node: ast.AST | None,
    aliases: dict[str, str],
    path_names: set[str],
) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in path_names
    if isinstance(node, ast.Call):
        target = _canonical_name(node.func, aliases)
        if target == "pathlib.Path":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "absolute",
            "expanduser",
            "resolve",
        }:
            return _is_path_expression(node.func.value, aliases, path_names)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_path_expression(node.left, aliases, path_names)
    return False


def _path_names(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
            if node.args.vararg is not None:
                args.append(node.args.vararg)
            if node.args.kwarg is not None:
                args.append(node.args.kwarg)
            for arg in args:
                if _annotation_name(arg.annotation, aliases) == "pathlib.Path":
                    names.add(arg.arg)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            if _is_path_expression(expression, aliases, names) and local not in names:
                names.add(local)
                changed = True
    return names


def _mutation_target(
    node: ast.AST | None,
    aliases: dict[str, str],
    path_names: set[str],
    callable_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in callable_aliases:
        return callable_aliases[node.id]

    direct = _canonical_name(node, aliases)
    if direct in MODULE_MUTATIONS:
        return direct

    if isinstance(node, ast.Attribute):
        if (
            node.attr in PATH_MUTATIONS
            and _is_path_expression(node.value, aliases, path_names)
        ):
            return f"pathlib.Path.{node.attr}"

    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in PATH_MUTATIONS
            and _is_path_expression(node.args[0], aliases, path_names)
        ):
            return f"pathlib.Path.{node.args[1].value}"

    return None


def _callable_aliases(
    tree: ast.AST,
    aliases: dict[str, str],
    path_names: set[str],
) -> dict[str, str]:
    callable_aliases: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            value = _mutation_target(
                expression,
                aliases,
                path_names,
                callable_aliases,
            )
            if value and callable_aliases.get(local) != value:
                callable_aliases[local] = value
                changed = True
    return callable_aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    path_names = _path_names(tree, aliases)
    callable_aliases = _callable_aliases(tree, aliases, path_names)
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _mutation_target(
            node.func,
            aliases,
            path_names,
            callable_aliases,
        )
        if target:
            findings.append(
                f"line {node.lineno}: destructive filesystem mutation: {target}"
            )

    return sorted(set(findings))


def test_evaluation_library_has_no_destructive_filesystem_mutation() -> None:
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
        ("import os\ndef gate(path):\n    os.unlink(path)\n", "os.unlink"),
        ("from os import remove as rm\ndef gate(path):\n    rm(path)\n", "os.remove"),
        (
            "import os\nrm = getattr(os, 'remove')\ndef gate(path):\n    rm(path)\n",
            "os.remove",
        ),
        (
            "import shutil as sh\ndef gate(src, dst):\n    sh.move(src, dst)\n",
            "shutil.move",
        ),
        (
            "from pathlib import Path\ndef gate(path: Path):\n    path.unlink()\n",
            "pathlib.Path.unlink",
        ),
        (
            "from pathlib import Path\ndef gate(raw: str):\n"
            "    path = Path(raw).resolve()\n    path.chmod(0o600)\n",
            "pathlib.Path.chmod",
        ),
        (
            "from pathlib import Path\ndef gate(path: Path):\n"
            "    destroy = path.replace\n    destroy(Path('other'))\n",
            "pathlib.Path.replace",
        ),
        (
            "from pathlib import Path\ndef gate(path: Path):\n"
            "    getattr(path, 'rename')(Path('other'))\n",
            "pathlib.Path.rename",
        ),
        (
            "import os\ndef gate(path):\n"
            "    truncate = os.truncate\n    truncate(path, 0)\n",
            "os.truncate",
        ),
    ],
)
def test_contract_rejects_destructive_filesystem_mutations(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_preserves_canonical_output_creation() -> None:
    source = """
from pathlib import Path

def emit(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
"""
    assert scan_source(source) == []


def test_contract_preserves_read_only_path_operations_and_string_replace() -> None:
    source = """
from pathlib import Path

def inspect(path: Path, label: str) -> tuple[bool, str]:
    return path.exists(), label.replace("old", "new")
"""
    assert scan_source(source) == []
