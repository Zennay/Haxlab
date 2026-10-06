from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "tools" / "imitation_selection.js"
EXTRACTOR = ROOT / "tools" / "extract_imitation.js"


def _parse_selection(raw_value: str) -> dict[str, object]:
    script = r"""
const { parseSelectedPlayerMapJson } = require(process.argv[1]);
try {
  const selected = parseSelectedPlayerMapJson(process.argv[2]);
  process.stdout.write(JSON.stringify({
    ok: true,
    entries: Array.from(selected.entries()),
  }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    name: error.name,
    message: error.message,
  }));
}
"""
    completed = subprocess.run(
        ["node", "-e", script, str(PARSER), raw_value],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_imitation_selection_accepts_canonical_player_map() -> None:
    result = _parse_selection(
        '{"0":"name:keeper","7":"auth:abc123","12":"name:alpha beta"}'
    )

    assert result == {
        "ok": True,
        "entries": [
            [0, "name:keeper"],
            [7, "auth:abc123"],
            [12, "name:alpha beta"],
        ],
    }


@pytest.mark.parametrize(
    "raw_value",
    [
        "",
        "null",
        "[]",
        "7",
        '"player"',
        "{}",
        "{not-json}",
        '{"01":"name:alpha"}',
        '{"-1":"name:alpha"}',
        '{"1.5":"name:alpha"}',
        '{"9007199254740992":"name:alpha"}',
        '{"7":null}',
        '{"7":7}',
        '{"7":""}',
        '{"7":"   "}',
    ],
)
def test_imitation_selection_rejects_malformed_or_coercible_input(
    raw_value: str,
) -> None:
    result = _parse_selection(raw_value)

    assert result["ok"] is False
    assert result["name"] in {"TypeError", "RangeError"}
    assert "selectedPlayerMapJson" in str(result["message"])


def test_imitation_extractor_uses_strict_selection_parser() -> None:
    source = EXTRACTOR.read_text(encoding="utf-8")

    assert 'require("./imitation_selection")' in source
    assert "parseSelectedPlayerMapJson(selectedPlayerMapJson)" in source
    assert "Object.entries(JSON.parse(selectedPlayerMapJson))" not in source
    assert "[Number(playerId), String(identity)]" not in source


def test_imitation_selection_javascript_is_syntax_valid() -> None:
    for source in (PARSER, EXTRACTOR):
        completed = subprocess.run(
            ["node", "--check", str(source)],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
