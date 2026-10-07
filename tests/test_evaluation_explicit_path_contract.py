from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = ROOT / "src" / "haxlab" / "evaluation"

_FORBIDDEN_PATH_METHODS = {"glob", "rglob", "iterdir"}
_FORBIDDEN_MODULE_CALLS = {
    "glob": {"glob", "iglob"},
    "os": {"listdir", "scandir", "walk"},
}


def _violations(source: str, *, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    module_aliases: dict[str, str] = {}
    direct_aliases: set[str] = set()
    assigned_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_MODULE_CALLS:
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in _FORBIDDEN_MODULE_CALLS:
            forbidden = _FORBIDDEN_MODULE_CALLS[node.module]
            for alias in node.names:
                if alias.name in forbidden:
                    direct_aliases.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Attribute) or not isinstance(value.value, ast.Name):
            continue
        module = module_aliases.get(value.value.id)
        if module is None or value.attr not in _FORBIDDEN_MODULE_CALLS[module]:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                assigned_aliases.add(target.id)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if isinstance(func, ast.Name) and (
            func.id in direct_aliases or func.id in assigned_aliases
        ):
            violations.append(f"{filename}:{node.lineno}:ambient_filesystem_discovery:{func.id}")
            continue

        if not isinstance(func, ast.Attribute):
            continue

        if func.attr in _FORBIDDEN_PATH_METHODS:
            violations.append(f"{filename}:{node.lineno}:ambient_filesystem_discovery:{func.attr}")
            continue

        if isinstance(func.value, ast.Name):
            module = module_aliases.get(func.value.id)
            if module is not None and func.attr in _FORBIDDEN_MODULE_CALLS[module]:
                violations.append(
                    f"{filename}:{node.lineno}:ambient_filesystem_discovery:"
                    f"{module}.{func.attr}"
                )

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('/tmp').glob('*.json')\n",
        "from pathlib import Path\nPath('/tmp').rglob('*.json')\n",
        "from pathlib import Path\nPath('/tmp').iterdir()\n",
        "import os\nos.walk('/tmp')\n",
        "import os as operating_system\noperating_system.listdir('/tmp')\n",
        "from os import scandir as scan\nscan('/tmp')\n",
        "import glob as glob_module\nglob_module.iglob('/tmp/*')\n",
        "from glob import glob as discover\ndiscover('/tmp/*')\n",
        "import os\ndiscover = os.walk\ndiscover('/tmp')\n",
    ],
)
def test_contract_rejects_ambient_filesystem_discovery(source: str) -> None:
    assert _violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('/tmp/evidence.json').read_text()\n",
        "from pathlib import Path\nPath('/tmp/evidence.json').open('rb')\n",
        "import os\nos.stat('/tmp/evidence.json')\n",
    ],
)
def test_contract_allows_explicit_evidence_paths(source: str) -> None:
    assert _violations(source) == []


def test_evaluation_package_uses_only_explicit_evidence_paths() -> None:
    violations: list[str] = []
    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(
            _violations(path.read_text(encoding="utf-8"), filename=relative)
        )

    assert violations == [], "\n".join(violations)
