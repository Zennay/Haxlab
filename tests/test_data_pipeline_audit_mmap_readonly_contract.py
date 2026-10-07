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
MMAP_CONSTRUCTOR = "mmap.mmap"
SAFE_ACCESS = {"mmap.ACCESS_COPY", "mmap.ACCESS_READ"}


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


def _tracked_mmap_name(name: str | None) -> bool:
    return bool(name and (name == "mmap" or name.startswith("mmap.")))


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "mmap":
                    aliases[item.asname or "mmap"] = "mmap"
        elif isinstance(node, ast.ImportFrom) and node.module == "mmap":
            for item in node.names:
                if item.name == "*":
                    continue
                aliases[item.asname or item.name] = f"mmap.{item.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            target = _canonical_name(expression, aliases)
            if _tracked_mmap_name(target) and aliases.get(local) != target:
                aliases[local] = target
                changed = True

    return aliases


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _mmap_finding(
    call: ast.Call,
    aliases: dict[str, str],
) -> str | None:
    target = _canonical_name(call.func, aliases)
    if target != MMAP_CONSTRUCTOR:
        return None

    access_node = _keyword(call, "access")
    access = _canonical_name(access_node, aliases)
    if access not in SAFE_ACCESS:
        rendered = access or "<omitted-or-dynamic>"
        return f"line {call.lineno}: mmap access not proven read-only: {rendered}"

    if _keyword(call, "prot") is not None or _keyword(call, "flags") is not None:
        return (
            f"line {call.lineno}: mmap mixes safe access with explicit "
            "prot/flags"
        )

    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings = [
        finding
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        if (finding := _mmap_finding(node, aliases)) is not None
    ]
    return sorted(set(findings))


def test_data_pipeline_auditors_use_only_readonly_memory_maps() -> None:
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
            "import mmap\ndef audit(fd):\n    mmap.mmap(fd, 4096)\n",
            "<omitted-or-dynamic>",
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    mmap.mmap(fd, 4096, 0, 0, mmap.ACCESS_READ)\n",
            "<omitted-or-dynamic>",
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    mmap.mmap(fd, 4096, access=mmap.ACCESS_WRITE)\n",
            "mmap.ACCESS_WRITE",
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    mmap.mmap(fd, 4096, access=mmap.ACCESS_DEFAULT)\n",
            "mmap.ACCESS_DEFAULT",
        ),
        (
            "import mmap\ndef audit(fd, access):\n"
            "    mmap.mmap(fd, 4096, access=access)\n",
            "access",
        ),
        (
            "from mmap import mmap as map_file, ACCESS_WRITE as WRITE\n"
            "def audit(fd):\n    map_file(fd, 4096, access=WRITE)\n",
            "mmap.ACCESS_WRITE",
        ),
        (
            "import mmap as mm\ndef audit(fd):\n"
            "    mapper = mm.mmap\n"
            "    mapper(fd, 4096, access=mm.ACCESS_WRITE)\n",
            "mmap.ACCESS_WRITE",
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    mmap.mmap(fd, 4096, access=mmap.ACCESS_READ, "
            "prot=mmap.PROT_WRITE)\n",
            "prot/flags",
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    safe = mmap.ACCESS_READ\n"
            "    mmap.mmap(fd, 4096, access=safe, flags=mmap.MAP_SHARED)\n",
            "prot/flags",
        ),
    ],
)
def test_contract_rejects_unproven_or_writable_mmap(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


@pytest.mark.parametrize(
    "source",
    [
        (
            "import mmap\ndef audit(fd):\n"
            "    return mmap.mmap(fd, 4096, access=mmap.ACCESS_READ)\n"
        ),
        (
            "from mmap import mmap as map_file, ACCESS_COPY as COPY\n"
            "def audit(fd):\n    return map_file(fd, 4096, access=COPY)\n"
        ),
        (
            "import mmap\ndef audit(fd):\n"
            "    safe = getattr(mmap, 'ACCESS_READ')\n"
            "    return mmap.mmap(fd, 4096, access=safe)\n"
        ),
    ],
)
def test_contract_allows_explicit_non_mutating_mmap(source: str) -> None:
    assert scan_source(source) == []


def test_contract_ignores_unrelated_mmap_method_names() -> None:
    source = """
class LocalBuffer:
    def mmap(self) -> bytes:
        return b"evidence"

def audit(buffer: LocalBuffer) -> bytes:
    return buffer.mmap()
"""
    assert scan_source(source) == []
