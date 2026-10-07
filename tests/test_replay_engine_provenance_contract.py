from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
DECODER = ROOT / "tools" / "decode_replay.js"

_EXACT_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _node_haxball_version() -> str:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    dependencies = package.get("dependencies")

    assert isinstance(dependencies, dict)
    version = dependencies.get("node-haxball")
    assert isinstance(version, str)
    return version


def test_node_replay_engine_is_exactly_version_pinned() -> None:
    version = _node_haxball_version()

    assert _EXACT_SEMVER.fullmatch(version), (
        "node-haxball must remain an exact semantic-version pin so replay "
        f"derivation is reproducible; got {version!r}"
    )


def test_decoder_provenance_matches_pinned_node_engine() -> None:
    version = _node_haxball_version()
    decoder_source = DECODER.read_text(encoding="utf-8")
    declared = re.findall(r'decoder:\s*"([^"]+)"', decoder_source)

    assert declared == [f"node-haxball@{version}"], (
        "decoder provenance must identify the exact node-haxball version "
        f"pinned in package.json; found {declared!r}"
    )
