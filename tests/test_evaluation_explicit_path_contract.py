from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = ROOT / "src" / "haxlab" / "evaluation"
PROOF_WORKFLOW = ROOT / ".github" / "workflows" / "evaluation-explicit-path-contract-proof.yml"

DIRECT_DISCOVERY_CALLS = {
    "glob.glob",
    "glob.iglob",
    "os.fwalk",
    "os.listdir",
    "os.scandir",
    "os.walk",
    "pathlib.Path.glob",
    "pathlib.Path.iterdir",
    "pathlib.Path.rglob",
    "pathlib.Path.walk",
}
PATH_DISCOVERY_METHODS = {"glob", "iterdir", "rglob", "walk"}
PATH_CLASS_FACTORIES = {"pathlib.Path.cwd", "pathlib.Path.home"}
PATH_RETURNING_METHODS = {
    "absolute",
    "expanduser",
    "joinpath",
    "resolve",
    "with_name",
    "with_stem",
    "with_suffix",
}
TRACKED_MODULES = {"builtins", "glob", "os", "pathlib"}


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
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            value = _canonical_name(expression, aliases)
            if value in DIRECT_DISCOVERY_CALLS and aliases.get(local) != value:
                aliases[local] = value
                changed = True
    return aliases


def _path_object(
    node: ast.AST | None,
    aliases: dict[str, str],
    path_objects: set[str],
) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in path_objects
    if isinstance(node, ast.Call):
        canonical = _canonical_name(node.func, aliases)
        if canonical == "pathlib.Path" or canonical in PATH_CLASS_FACTORIES:
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in PATH_RETURNING_METHODS
            and _path_object(node.func.value, aliases, path_objects)
        ):
            return True
    if isinstance(node, ast.Attribute):
        return node.attr == "parent" and _path_object(
            node.value,
            aliases,
            path_objects,
        )
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents"
    ):
        return _path_object(node.value.value, aliases, path_objects)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_object(node.left, aliases, path_objects)
    return False


def _path_objects(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    objects: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            if _path_object(expression, aliases, objects) and local not in objects:
                objects.add(local)
                changed = True
    return objects


def _callable_name(
    node: ast.AST | None,
    aliases: dict[str, str],
    path_objects: set[str],
    callable_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in callable_aliases:
        return callable_aliases[node.id]

    direct = _canonical_name(node, aliases)
    if direct in DIRECT_DISCOVERY_CALLS:
        return direct

    if isinstance(node, ast.Attribute):
        if _path_object(node.value, aliases, path_objects) and (
            node.attr in PATH_DISCOVERY_METHODS
        ):
            return f"pathlib.Path.{node.attr}"

    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            method = node.args[1].value
            if _path_object(node.args[0], aliases, path_objects) and (
                method in PATH_DISCOVERY_METHODS
            ):
                return f"pathlib.Path.{method}"

    return None


def _callable_aliases(
    tree: ast.AST,
    aliases: dict[str, str],
    path_objects: set[str],
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
            value = _callable_name(
                expression,
                aliases,
                path_objects,
                callable_aliases,
            )
            if value and callable_aliases.get(local) != value:
                callable_aliases[local] = value
                changed = True
    return callable_aliases


def _violations(source: str, *, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    path_objects = _path_objects(tree, aliases)
    callable_aliases = _callable_aliases(tree, aliases, path_objects)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _callable_name(
            node.func,
            aliases,
            path_objects,
            callable_aliases,
        )
        if target in DIRECT_DISCOVERY_CALLS or (
            target is not None and target.startswith("pathlib.Path.")
        ):
            violations.append(
                f"{filename}:{node.lineno}:ambient_filesystem_discovery:{target}"
            )

    return sorted(set(violations))


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('/tmp').glob('*.json')\n",
        "from pathlib import Path\nPath('/tmp').rglob('*.json')\n",
        "from pathlib import Path\nPath('/tmp').iterdir()\n",
        "from pathlib import Path\nPath('/tmp').walk()\n",
        "from pathlib import Path\np = Path('/tmp')\nPath.glob(p, '*')\n",
        "from pathlib import Path\np = Path('/tmp')\ndiscover = Path.rglob\ndiscover(p, '*')\n",
        "from pathlib import Path\np = Path('/tmp')\ndiscover = getattr(Path, 'iterdir')\ndiscover(p)\n",
        "import pathlib as pl\np = pl.Path('/tmp')\np.rglob('*')\n",
        "import os\nos.walk('/tmp')\n",
        "import os\nos.fwalk('/tmp')\n",
        "import os as operating_system\noperating_system.listdir('/tmp')\n",
        "from os import scandir as scan\nscan('/tmp')\n",
        "import glob as glob_module\nglob_module.iglob('/tmp/*')\n",
        "from glob import glob as discover\ndiscover('/tmp/*')\n",
        "import os\ndiscover = os.walk\ndiscover('/tmp')\n",
        "import os\ndiscover = getattr(os, 'walk')\ndiscover('/tmp')\n",
        "from pathlib import Path\np = Path('/tmp')\ndiscover = p.glob\ndiscover('*')\n",
        (
            "from pathlib import Path\np = Path('/tmp')\n"
            "discover = getattr(p, 'rglob')\ndiscover('*')\n"
        ),
    ],
)
def test_contract_rejects_ambient_filesystem_discovery(source: str) -> None:
    assert _violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath.cwd().glob('*.json')\n",
        "from pathlib import Path\nPath.home().iterdir()\n",
        "from pathlib import Path\nPath('/tmp').parent.rglob('*.json')\n",
        "from pathlib import Path\nPath('/tmp').resolve().walk()\n",
        (
            "from pathlib import Path\np = Path('/tmp')\n"
            "child = p / 'cache'\nchild.glob('*.json')\n"
        ),
        (
            "from pathlib import Path\np = Path('/tmp')\n"
            "p.parents[0].iterdir()\n"
        ),
    ],
)
def test_contract_rejects_discovery_from_derived_path_objects(source: str) -> None:
    assert _violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('/tmp/evidence.json').read_text()\n",
        "from pathlib import Path\nPath('/tmp/evidence.json').open('rb')\n",
        "import os\nos.stat('/tmp/evidence.json')\n",
        (
            "class ExplicitIndex:\n"
            "    def walk(self):\n"
            "        return ('already-bound-entry',)\n"
            "ExplicitIndex().walk()\n"
        ),
    ],
)
def test_contract_allows_explicit_evidence_paths_and_unrelated_methods(
    source: str,
) -> None:
    assert _violations(source) == []


def test_evaluation_package_uses_only_explicit_evidence_paths() -> None:
    paths = sorted(EVALUATION_ROOT.rglob("*.py"))
    assert paths, "evaluation package unexpectedly contains no Python modules"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(
            _violations(path.read_text(encoding="utf-8"), filename=relative)
        )

    assert violations == [], "\n".join(violations)


def test_proof_workflow_is_manual_and_exact_live_head_bound() -> None:
    workflow = PROOF_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert 'test "$HEAD_SHA" = "${GITHUB_SHA}"' in workflow
    assert 'case "${GITHUB_REF}" in' in workflow
    assert "refs/heads/*)" in workflow
    assert 'git ls-remote --exit-code origin "${GITHUB_REF}"' in workflow
    assert 'if [ "$LIVE_SHA" != "${GITHUB_SHA}" ]; then' in workflow
