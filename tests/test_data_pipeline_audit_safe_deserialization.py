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

_FORBIDDEN_IMPORT_ROOTS = {
    "cloudpickle",
    "dill",
    "joblib",
    "marshal",
    "pickle",
    "shelve",
}
_FORBIDDEN_CALLS = {
    "compile",
    "builtins.compile",
    "eval",
    "builtins.eval",
    "exec",
    "builtins.exec",
    "pandas.read_pickle",
    "torch.load",
    "torch.jit.load",
    "yaml.full_load",
    "yaml.full_load_all",
    "yaml.load",
    "yaml.load_all",
    "yaml.unsafe_load",
    "yaml.unsafe_load_all",
}


def _qualname(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


class _AliasCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.forbidden_imports: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self.aliases[local] = target
            root = item.name.split(".", 1)[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                self.forbidden_imports.append(
                    f"{node.lineno}:executable_deserializer_import:{root}"
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".", 1)[0]
        if root in _FORBIDDEN_IMPORT_ROOTS:
            self.forbidden_imports.append(
                f"{node.lineno}:executable_deserializer_import:{root}"
            )
        for item in node.names:
            if item.name == "*":
                continue
            local = item.asname or item.name
            self.aliases[local] = f"{module}.{item.name}" if module else item.name


class _SafeDeserializationVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.violations: list[str] = []

    def _add(self, node: ast.AST, reason: str) -> None:
        self.violations.append(f"{getattr(node, 'lineno', 0)}:{reason}")

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)

        if name in _FORBIDDEN_CALLS:
            self._add(node, f"unsafe_deserialization_call:{name}")

        if name == "numpy.load":
            allow_pickle = None
            for keyword in node.keywords:
                if keyword.arg == "allow_pickle":
                    allow_pickle = keyword.value
                    break
            if allow_pickle is not None:
                if not (
                    isinstance(allow_pickle, ast.Constant)
                    and allow_pickle.value is False
                ):
                    self._add(node, "numpy_pickle_load")

        self.generic_visit(node)


def _deserialization_violations(
    source: str,
    *,
    filename: str = "<contract>",
) -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _AliasCollector()
    aliases.visit(tree)
    visitor = _SafeDeserializationVisitor(aliases.aliases)
    visitor.visit(tree)
    return sorted(set([*aliases.forbidden_imports, *visitor.violations]))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
        ),
        key=lambda path: path.as_posix(),
    )


def test_data_pipeline_auditors_use_data_only_deserialization() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline audit module"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for violation in _deserialization_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        ):
            violations.append(f"{relative}:{violation}")

    assert violations == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("import pickle\nvalue = pickle.loads(raw)", "executable_deserializer_import:pickle"),
        ("from marshal import loads\nvalue = loads(raw)", "executable_deserializer_import:marshal"),
        ("import shelve\ndb = shelve.open(path)", "executable_deserializer_import:shelve"),
        ("import dill\nvalue = dill.loads(raw)", "executable_deserializer_import:dill"),
        ("import cloudpickle\nvalue = cloudpickle.loads(raw)", "executable_deserializer_import:cloudpickle"),
        ("import joblib\nvalue = joblib.load(path)", "executable_deserializer_import:joblib"),
        ("value = eval(text)", "unsafe_deserialization_call:eval"),
        ("exec(text)", "unsafe_deserialization_call:exec"),
        ("code = compile(text, '<audit>', 'exec')", "unsafe_deserialization_call:compile"),
        ("import yaml\nvalue = yaml.load(raw, Loader=loader)", "unsafe_deserialization_call:yaml.load"),
        ("from yaml import full_load as load\nvalue = load(raw)", "unsafe_deserialization_call:yaml.full_load"),
        ("import torch\nvalue = torch.load(path)", "unsafe_deserialization_call:torch.load"),
        ("from torch.jit import load\nvalue = load(path)", "unsafe_deserialization_call:torch.jit.load"),
        ("import pandas as pd\nvalue = pd.read_pickle(path)", "unsafe_deserialization_call:pandas.read_pickle"),
        ("import numpy as np\nvalue = np.load(path, allow_pickle=True)", "numpy_pickle_load"),
        ("import numpy as np\nvalue = np.load(path, allow_pickle=policy)", "numpy_pickle_load"),
    ],
)
def test_contract_rejects_executable_or_object_deserialization(
    source: str,
    reason: str,
) -> None:
    violations = _deserialization_violations(source)
    assert any(reason in violation for violation in violations), violations


def test_contract_allows_data_only_parsers() -> None:
    source = """
import ast
import json
import numpy as np
import tomllib
import yaml

json_value = json.loads(raw_json)
toml_value = tomllib.loads(raw_toml)
yaml_value = yaml.safe_load(raw_yaml)
yaml_values = list(yaml.safe_load_all(raw_yaml_stream))
array_default = np.load(path)
array_explicit = np.load(path, allow_pickle=False)
literal_value = ast.literal_eval("[1, 2, 3]")
"""
    assert _deserialization_violations(source) == []
