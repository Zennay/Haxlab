from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
PACKAGE_LOCK = ROOT / "package-lock.json"
_SHA512_INTEGRITY = re.compile(r"^sha512-[A-Za-z0-9+/]+={0,2}$")


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_node_lockfile_binds_root_replay_engine_dependency() -> None:
    package = _json(PACKAGE_JSON)
    lock = _json(PACKAGE_LOCK)

    dependencies = package.get("dependencies")
    assert isinstance(dependencies, dict)
    node_haxball = dependencies.get("node-haxball")
    assert isinstance(node_haxball, str)

    assert lock.get("name") == package.get("name")
    assert lock.get("version") == package.get("version")
    assert type(lock.get("lockfileVersion")) is int
    assert int(lock["lockfileVersion"]) >= 2

    packages = lock.get("packages")
    assert isinstance(packages, dict)
    root_package = packages.get("")
    assert isinstance(root_package, dict)
    assert root_package.get("dependencies") == {"node-haxball": node_haxball}


def test_node_lockfile_resolves_exact_replay_engine_with_registry_integrity() -> None:
    package = _json(PACKAGE_JSON)
    lock = _json(PACKAGE_LOCK)

    dependencies = package["dependencies"]
    assert isinstance(dependencies, dict)
    expected_version = dependencies["node-haxball"]
    assert isinstance(expected_version, str)

    packages = lock["packages"]
    assert isinstance(packages, dict)
    resolved_package = packages.get("node_modules/node-haxball")
    assert isinstance(resolved_package, dict)

    assert resolved_package.get("version") == expected_version

    resolved = resolved_package.get("resolved")
    integrity = resolved_package.get("integrity")
    assert isinstance(resolved, str)
    assert resolved.startswith(
        "https://registry.npmjs.org/node-haxball/-/node-haxball-"
    )
    assert isinstance(integrity, str)
    assert _SHA512_INTEGRITY.fullmatch(integrity)
