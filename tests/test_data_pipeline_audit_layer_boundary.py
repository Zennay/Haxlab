from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
AUDIT_ROOTS = (
    SRC_ROOT / "haxlab" / "ingestion",
    SRC_ROOT / "haxlab" / "learning",
    SRC_ROOT / "haxlab" / "runtime",
)

FORBIDDEN_HIGHER_LAYER_PREFIXES = (
    "haxlab.coaching",
    "haxlab.evaluation",
    "haxlab.live",
    "haxlab.research",
    "haxlab.rollout",
    "haxlab.training",
)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_HIGHER_LAYER_PREFIXES
    )


def _resolve_import_from(
    current_module: str,
    *,
    level: int,
    imported_module: str | None,
) -> str:
    if level == 0:
        return imported_module or ""

    package_parts = current_module.split(".")[:-1]
    climb = level - 1
    if climb > len(package_parts):
        return ""

    base_parts = package_parts[: len(package_parts) - climb]
    if imported_module:
        base_parts.extend(imported_module.split("."))
    return ".".join(base_parts)


class DataPipelineAuditLayerBoundaryVisitor(ast.NodeVisitor):
    def __init__(self, *, module_name: str) -> None:
        self.module_name = module_name
        self.violations: list[str] = []

    def _record(self, *, lineno: int, module: str) -> None:
        self.violations.append(
            f"line {lineno}: data-pipeline auditor must not import higher-layer module {module!r}"
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_forbidden(alias.name):
                self._record(lineno=node.lineno, module=alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target = _resolve_import_from(
            self.module_name,
            level=node.level,
            imported_module=node.module,
        )

        if target and _is_forbidden(target):
            self._record(lineno=node.lineno, module=target)

        for alias in node.names:
            if alias.name == "*":
                continue
            candidate = f"{target}.{alias.name}" if target else alias.name
            if _is_forbidden(candidate):
                self._record(lineno=node.lineno, module=candidate)

        self.generic_visit(node)


def scan_source(
    source: str,
    *,
    module_name: str = "haxlab.runtime.example_audit",
    filename: str = "<memory>",
) -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = DataPipelineAuditLayerBoundaryVisitor(module_name=module_name)
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC_ROOT).with_suffix("").parts)


def test_data_pipeline_auditors_do_not_import_higher_control_layers() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(
            path.read_text(encoding="utf-8"),
            module_name=_module_name(path),
            filename=relative,
        )
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "module_name", "expected"),
    [
        (
            "from haxlab.coaching import advisor\n",
            "haxlab.learning.manifest_audit",
            "haxlab.coaching",
        ),
        (
            "import haxlab.evaluation.promotion as promotion\n",
            "haxlab.runtime.state_audit",
            "haxlab.evaluation.promotion",
        ),
        (
            "from haxlab.live.inference import load_model\n",
            "haxlab.ingestion.dataset_audit",
            "haxlab.live.inference",
        ),
        (
            "from haxlab import rollout\n",
            "haxlab.learning.shard_audit",
            "haxlab.rollout",
        ),
        (
            "from haxlab import research as autonomous_research\n",
            "haxlab.runtime.archive_audit",
            "haxlab.research",
        ),
        (
            "from haxlab.training.runner import train\n",
            "haxlab.learning.manifest_audit",
            "haxlab.training.runner",
        ),
        (
            "from .. import evaluation\n",
            "haxlab.runtime.archive_audit",
            "haxlab.evaluation",
        ),
        (
            "from ..rollout import arena\n",
            "haxlab.ingestion.dataset_audit",
            "haxlab.rollout",
        ),
    ],
)
def test_contract_rejects_higher_layer_imports(
    source: str,
    module_name: str,
    expected: str,
) -> None:
    findings = scan_source(source, module_name=module_name)
    assert any(expected in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("source", "module_name"),
    [
        (
            "from haxlab.runtime.state import RuntimeState\n",
            "haxlab.runtime.archive_audit",
        ),
        (
            "from haxlab.learning import shard_audit\n",
            "haxlab.learning.shard_bundle_audit",
        ),
        (
            "from haxlab.hashing import sha256_file\n",
            "haxlab.ingestion.dataset_audit",
        ),
        (
            "from haxlab.analysis.roles import ROLES_4V4\n",
            "haxlab.learning.manifest_audit",
        ),
        (
            "from ..hashing import sha256_file\n",
            "haxlab.runtime.archive_audit",
        ),
        (
            "import json\nfrom pathlib import Path\n",
            "haxlab.ingestion.dataset_audit",
        ),
    ],
)
def test_contract_preserves_local_and_read_only_dependencies(
    source: str,
    module_name: str,
) -> None:
    assert scan_source(source, module_name=module_name) == []
