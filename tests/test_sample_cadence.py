from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "tools" / "sample_cadence.js"


def _parse_sample_every(raw_value: str | None) -> dict[str, object]:
    script = r"""
const { parseSampleEveryTicks } = require(process.argv[1]);
const raw = process.argv[2] === "__OMITTED__" ? undefined : process.argv[2];
try {
  process.stdout.write(JSON.stringify({
    ok: true,
    value: parseSampleEveryTicks(raw),
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
        [
            "node",
            "-e",
            script,
            str(PARSER),
            "__OMITTED__" if raw_value is None else raw_value,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 6),
        ("1", 1),
        ("6", 6),
        ("600", 600),
    ],
)
def test_producer_sample_cadence_accepts_only_canonical_positive_integers(
    raw_value: str | None,
    expected: int,
) -> None:
    result = _parse_sample_every(raw_value)

    assert result == {"ok": True, "value": expected}


@pytest.mark.parametrize(
    "raw_value",
    [
        "",
        "0",
        "-1",
        "+6",
        "01",
        "6.0",
        "6oops",
        " 6",
        "6 ",
        "true",
        "9007199254740992",
    ],
)
def test_producer_sample_cadence_rejects_coercible_or_unsafe_values(
    raw_value: str,
) -> None:
    result = _parse_sample_every(raw_value)

    assert result["ok"] is False
    assert result["name"] in {"TypeError", "RangeError"}
    assert "sampleEveryTicks" in str(result["message"])


@pytest.mark.parametrize(
    "producer",
    [
        ROOT / "tools" / "decode_replay.js",
        ROOT / "tools" / "extract_imitation.js",
    ],
)
def test_replay_producers_use_shared_cadence_parser(producer: Path) -> None:
    source = producer.read_text(encoding="utf-8")

    assert 'require("./sample_cadence")' in source
    assert "parseSampleEveryTicks(" in source
    assert "Number.parseInt" not in source


@pytest.mark.parametrize(
    "producer",
    [
        ROOT / "tools" / "decode_replay.js",
        ROOT / "tools" / "extract_imitation.js",
        ROOT / "tools" / "sample_cadence.js",
    ],
)
def test_replay_producer_javascript_is_syntax_valid(producer: Path) -> None:
    completed = subprocess.run(
        ["node", "--check", str(producer)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
