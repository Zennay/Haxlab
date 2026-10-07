from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "tools" / "extract_imitation_args.js"


def _parse(raw: str) -> subprocess.CompletedProcess[str]:
    script = r"""
const parserPath = process.argv[1];
const raw = process.argv[2];
const { parseSelectedPlayerMapJson } = require(parserPath);

try {
  const parsed = parseSelectedPlayerMapJson(raw);
  process.stdout.write(JSON.stringify(Object.fromEntries(
    Array.from(parsed.entries()).map(([playerId, identity]) => [
      String(playerId),
      identity,
    ]),
  )));
} catch (error) {
  process.stderr.write(error && error.message ? error.message : String(error));
  process.exit(7);
}
"""
    return subprocess.run(
        ["node", "-e", script, str(PARSER), raw],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_imitation_player_map_preserves_valid_canonical_entries() -> None:
    completed = _parse('{"0":"player-zero","7":"player-seven","42":" Player 42 "}')

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "0": "player-zero",
        "7": "player-seven",
        "42": " Player 42 ",
    }


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("[]", "not_object"),
        ("null", "not_object"),
        ('"player"', "not_object"),
        ("42", "not_object"),
        ("{}", "empty"),
        ('{"01":"player"}', "invalid_player_id"),
        '{"+1":"player"}',
        '{" 1":"player"}',
        '{"1 ":"player"}',
        '{"1.0":"player"}',
        '{"1e2":"player"}',
        '{"9007199254740992":"player"}',
    ],
)
def test_imitation_player_map_rejects_invalid_container_or_player_ids(
    raw: str,
    reason: str | None = None,
) -> None:
    if reason is None:
        reason = "invalid_player_id"

    completed = _parse(raw)

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert completed.stderr == f"invalid_selected_player_map:{reason}"


@pytest.mark.parametrize(
    "raw",
    [
        '{"1":null}',
        '{"1":7}',
        '{"1":true}',
        '{"1":{}}',
        '{"1":[]}',
        '{"1":""}',
        '{"1":"   "}',
    ],
)
def test_imitation_player_map_rejects_non_string_or_blank_identities(raw: str) -> None:
    completed = _parse(raw)

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert completed.stderr == "invalid_selected_player_map:invalid_identity"


def test_imitation_player_map_rejects_invalid_json() -> None:
    completed = _parse("{not-json")

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert completed.stderr == "invalid_selected_player_map:invalid_json"


def test_imitation_extractor_uses_strict_player_map_parser() -> None:
    source = (ROOT / "tools" / "extract_imitation.js").read_text(encoding="utf-8")

    assert 'require("./extract_imitation_args")' in source
    assert "parseSelectedPlayerMapJson(selectedPlayerMapJson)" in source
    assert "Object.entries(JSON.parse(selectedPlayerMapJson))" not in source
    assert "String(identity)" not in source
