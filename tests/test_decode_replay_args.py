from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "tools" / "decode_replay_args.js"


def _parse(raw: str | None) -> subprocess.CompletedProcess[str]:
    script = r"""
const parserPath = process.argv[1];
const raw = process.argv.length > 2 ? process.argv[2] : undefined;
const {
  DEFAULT_SAMPLE_EVERY_TICKS,
  parseSampleEveryTicks,
} = require(parserPath);

try {
  process.stdout.write(JSON.stringify({
    defaultValue: DEFAULT_SAMPLE_EVERY_TICKS,
    value: parseSampleEveryTicks(raw),
  }));
} catch (error) {
  process.stderr.write(error && error.message ? error.message : String(error));
  process.exit(7);
}
"""
    command = ["node", "-e", script, str(PARSER)]
    if raw is not None:
        command.append(raw)
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_decoder_sample_cadence_defaults_to_six() -> None:
    completed = _parse(None)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"defaultValue": 6, "value": 6}


@pytest.mark.parametrize("raw", ["1", "6", "120", str((1 << 53) - 1)])
def test_decoder_sample_cadence_preserves_valid_positive_safe_integers(raw: str) -> None:
    completed = _parse(raw)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["value"] == int(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "-1",
        "+1",
        " 6",
        "6 ",
        "06",
        "6.0",
        "6.5",
        "6e2",
        "6foo",
        "Infinity",
        "NaN",
        "true",
        str(1 << 53),
    ],
)
def test_decoder_sample_cadence_rejects_noncanonical_or_unsafe_values(raw: str) -> None:
    completed = _parse(raw)

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert completed.stderr == "invalid_sample_every_ticks"


def test_decoder_entrypoint_uses_strict_cadence_parser() -> None:
    source = (ROOT / "tools" / "decode_replay.js").read_text(encoding="utf-8")

    assert 'require("./decode_replay_args")' in source
    assert "parseSampleEveryTicks(process.argv[3])" in source
    assert "Number.parseInt(process.argv[3]" not in source
