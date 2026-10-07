import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from haxlab.runtime import autonomy_status


def _valid_payload() -> dict[str, object]:
    return {
        "processing_pending": 2,
        "analysis_pending": 3,
        "processing_failed": 4,
        "analysis_failed": 5,
        "analysis_ok": 6,
        "analysis_version": "state-pass-v3",
        "unrelated_future_field": {"ok": True},
    }


def test_parse_autonomy_status_accepts_native_control_evidence() -> None:
    snapshot = autonomy_status.parse_autonomy_status(_valid_payload())

    assert snapshot.processing_pending == 2
    assert snapshot.analysis_pending == 3
    assert snapshot.processing_failed == 4
    assert snapshot.analysis_failed == 5
    assert snapshot.analysis_ok == 6
    assert snapshot.analysis_version == "state-pass-v3"
    assert snapshot.shell_fields() == "2 3 4 5 6 state-pass-v3"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("processing_pending", True),
        ("processing_pending", "2"),
        ("processing_pending", 2.0),
        ("processing_pending", -1),
        ("analysis_pending", False),
        ("analysis_pending", "3"),
        ("processing_failed", 1.0),
        ("analysis_failed", None),
        ("analysis_ok", "6"),
    ],
)
def test_parse_autonomy_status_rejects_coerced_or_negative_counters(
    field: str, value: object
) -> None:
    payload = _valid_payload()
    payload[field] = value

    with pytest.raises(autonomy_status.AutonomyStatusError):
        autonomy_status.parse_autonomy_status(payload)


@pytest.mark.parametrize(
    "version",
    [
        None,
        3,
        True,
        "",
        " state-pass-v3",
        "state-pass-v3 ",
        "state pass v3",
        "state-pass-v3\nforged",
        "state/pass/v3",
    ],
)
def test_parse_autonomy_status_rejects_noncanonical_analysis_version(
    version: object,
) -> None:
    payload = _valid_payload()
    payload["analysis_version"] = version

    with pytest.raises(autonomy_status.AutonomyStatusError):
        autonomy_status.parse_autonomy_status(payload)


def test_parse_autonomy_status_requires_every_control_field() -> None:
    for field in (
        "processing_pending",
        "analysis_pending",
        "processing_failed",
        "analysis_failed",
        "analysis_ok",
        "analysis_version",
    ):
        payload = _valid_payload()
        del payload[field]

        with pytest.raises(autonomy_status.AutonomyStatusError):
            autonomy_status.parse_autonomy_status(payload)


def test_main_emits_deterministic_shell_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        autonomy_status.sys,
        "stdin",
        io.StringIO(json.dumps(_valid_payload())),
    )

    assert autonomy_status.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "2 3 4 5 6 state-pass-v3\n"
    assert captured.err == ""


def test_main_fails_closed_with_machine_readable_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _valid_payload()
    payload["analysis_ok"] = "6"
    monkeypatch.setattr(
        autonomy_status.sys,
        "stdin",
        io.StringIO(json.dumps(payload)),
    )

    assert autonomy_status.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["error"] == "invalid_status_snapshot"
    assert "analysis_ok" in error["detail"]


def test_autonomy_tick_records_retryable_state_and_stops_on_invalid_snapshot(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    bin_dir = app_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(sys.executable)

    status_payload = _valid_payload()
    status_payload["analysis_ok"] = "6"
    status_cmd = bin_dir / "haxlab-status"
    status_cmd.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' {json.dumps(json.dumps(status_payload))}\n",
        encoding="utf-8",
    )
    status_cmd.chmod(0o755)

    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env.update(
        {
            "HAXLAB_APP_DIR": str(app_dir),
            "HAXLAB_STATE_DIR": str(state_dir),
            "HAXLAB_DERIVED_DIR": str(tmp_path / "derived"),
            "HAXLAB_MODELS_DIR": str(tmp_path / "models"),
            "PYTHONPATH": str(Path.cwd() / "src"),
        }
    )

    completed = subprocess.run(
        ["bash", "deploy/haxlab-autonomy-tick.sh"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    status = json.loads((state_dir / "autonomy-status.json").read_text())
    assert status["state"] == "FAILED_RETRYABLE"
    assert status["action"] == "invalid_status_snapshot"
    assert "downstream autonomy work was not started" in status["detail"]
    assert not (tmp_path / "derived").exists()
    assert not (tmp_path / "models").exists()



def test_autonomy_tick_records_retryable_state_on_status_command_failure(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    bin_dir = app_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(sys.executable)

    status_cmd = bin_dir / "haxlab-status"
    status_cmd.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'simulated status failure' >&2\n"
        "exit 23\n",
        encoding="utf-8",
    )
    status_cmd.chmod(0o755)

    state_dir = tmp_path / "state"
    derived_dir = tmp_path / "derived"
    models_dir = tmp_path / "models"
    env = os.environ.copy()
    env.update(
        {
            "HAXLAB_APP_DIR": str(app_dir),
            "HAXLAB_STATE_DIR": str(state_dir),
            "HAXLAB_DERIVED_DIR": str(derived_dir),
            "HAXLAB_MODELS_DIR": str(models_dir),
            "PYTHONPATH": str(Path.cwd() / "src"),
        }
    )

    completed = subprocess.run(
        ["bash", "deploy/haxlab-autonomy-tick.sh"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "simulated status failure" in completed.stderr
    status = json.loads((state_dir / "autonomy-status.json").read_text())
    assert status["state"] == "FAILED_RETRYABLE"
    assert status["action"] == "status_command_failed"
    assert "downstream autonomy work was not started" in status["detail"]
    assert not derived_dir.exists()
    assert not models_dir.exists()
