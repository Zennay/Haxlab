from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BANNED_IMPORT_ROOTS = {
    "cloudpickle",
    "dill",
    "joblib",
    "marshal",
    "pickle",
    "shelve",
}
DYNAMIC_CODE_CALLS = {
    "compile",
    "eval",
    "exec",
    "builtins.compile",
    "builtins.eval",
    "builtins.exec",
}
UNSAFE_YAML_CALLS = {
    "yaml.full_load",
    "yaml.full_load_all",
    "yaml.load",
    "yaml.load_all",
    "yaml.unsafe_load",
    "yaml.unsafe_load_all",
}
TORCH_OBJECT_LOAD_CALLS = {
    "torch.jit.load",
    "torch.load",
}


class SafeDeserializationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local_name] = alias.name
            root = alias.name.split(".", 1)[0]
            if root in BANNED_IMPORT_ROOTS:
                self.violations.append(
                    f"line {node.lineno}: forbidden executable-deserialization import {alias.name!r}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            self.generic_visit(node)
            return

        root = node.module.split(".", 1)[0]
        if root in BANNED_IMPORT_ROOTS:
            self.violations.append(
                f"line {node.lineno}: forbidden executable-deserialization import {node.module!r}"
            )

        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.aliases[local_name] = f"{node.module}.{alias.name}"

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target in DYNAMIC_CODE_CALLS:
            self.violations.append(
                f"line {node.lineno}: forbidden dynamic-code call {target!r}"
            )
        elif target in UNSAFE_YAML_CALLS:
            self.violations.append(
                f"line {node.lineno}: use yaml.safe_load/safe_load_all instead of {target!r}"
            )
        elif target in TORCH_OBJECT_LOAD_CALLS:
            self.violations.append(
                f"line {node.lineno}: object/model deserialization must stay outside evaluation gates: {target!r}"
            )
        elif target in {"numpy.load", "np.load"}:
            allow_pickle = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "allow_pickle"),
                None,
            )
            if not (
                isinstance(allow_pickle, ast.Constant)
                and allow_pickle.value is False
            ):
                self.violations.append(
                    f"line {node.lineno}: numpy.load requires literal allow_pickle=False"
                )

        self.generic_visit(node)

    def _qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            if parent is None:
                return None
            return f"{parent}.{node.attr}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = SafeDeserializationVisitor()
    visitor.visit(tree)
    return visitor.violations


def test_evaluation_package_has_no_executable_deserialization() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pickle\n", "forbidden executable-deserialization import"),
        ("from marshal import loads as decode\n", "forbidden executable-deserialization import"),
        ("from builtins import eval as run\nrun('1 + 1')\n", "forbidden dynamic-code call"),
        ("import yaml as y\ny.load('x: 1')\n", "yaml.safe_load"),
        ("from yaml import unsafe_load as load\nload('x: 1')\n", "yaml.safe_load"),
        ("import torch as t\nt.load('model.pt')\n", "deserialization must stay outside"),
        ("import numpy as np\nnp.load('array.npy')\n", "allow_pickle=False"),
        ("from numpy import load\nload('array.npy', allow_pickle=True)\n", "allow_pickle=False"),
        ("from joblib import load\n", "forbidden executable-deserialization import"),
    ],
)
def test_detector_rejects_unsafe_loading(source: str, expected: str) -> None:
    violations = scan_source(source)
    assert any(expected in violation for violation in violations), violations


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.loads('{\"ok\": true}')\n",
        "import tomllib\ntomllib.loads('value = 1')\n",
        "import yaml as y\ny.safe_load('x: 1')\n",
        "import numpy as np\nnp.load('array.npy', allow_pickle=False)\n",
        "from pathlib import Path\nPath('evidence.json').read_text(encoding='utf-8')\n",
    ],
)
def test_detector_allows_data_only_loading(source: str) -> None:
    assert scan_source(source) == []
