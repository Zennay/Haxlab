from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "update-service-recovery.sh"
UPDATE = ROOT / "deploy" / "update-vps.sh"


def _run_bash(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "haxlab-recovery-test", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _mock_systemctl(
    active_units: tuple[str, ...],
    *,
    fail_start: str | None = None,
    fail_stop: str | None = None,
) -> str:
    active_case = "|".join(active_units) or "__never__"
    start_failure = (
        f'if [[ "$2" == {shlex.quote(fail_start)} ]]; then return 1; fi'
        if fail_start is not None
        else ""
    )
    stop_failure = (
        f'if [[ "$2" == {shlex.quote(fail_stop)} ]]; then return 1; fi'
        if fail_stop is not None
        else ""
    )
    return f"""
systemctl() {{
  if [[ "$1" == "is-active" && "$2" == "--quiet" ]]; then
    case "$3" in
      {active_case}) return 0 ;;
      *) return 3 ;;
    esac
  fi
  if [[ "$1" == "stop" ]]; then
    if declare -p STOP_CALLS >/dev/null 2>&1; then
      printf '%s\\n' "$2" >> "$STOP_CALLS"
    fi
    {stop_failure}
    return 0
  fi
  if [[ "$1" == "start" ]]; then
    printf '%s\\n' "$2" >> "$CALLS"
    {start_failure}
    return 0
  fi
  return 97
}}
"""

def test_capture_and_restore_use_dependency_safe_reverse_stop_order(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(("haxlab-live-bot.service", "haxlab-analyzer.service", "haxlab-ingest.service"))}
haxlab_capture_update_service_state
printf 'active:%s\\n' "${{HAXLAB_UPDATE_ACTIVE_BEFORE[*]}}"
haxlab_restore_update_services
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "active:haxlab-live-bot.service "
        "haxlab-analyzer.service haxlab-ingest.service"
    )
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-analyzer.service",
        "haxlab-live-bot.service",
    ]


def test_failure_trap_restores_only_previously_active_services_and_exit_code(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(("haxlab-worker.service", "haxlab-ingest.service"))}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 23
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 23
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-worker.service",
    ]


def test_restore_failure_never_replaces_original_update_exit_code(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(("haxlab-live-bot.service", "haxlab-analyzer.service", "haxlab-ingest.service"), fail_start="haxlab-analyzer.service")}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 17
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 17
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-analyzer.service",
        "haxlab-live-bot.service",
    ]


def test_failure_with_no_previously_active_services_starts_nothing(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(())}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 9
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 9
    assert not calls.exists()


def test_successful_exit_does_not_run_recovery(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(("haxlab-worker.service", "haxlab-ingest.service"))}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 0
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 0, result.stderr
    assert not calls.exists()


def test_update_preparses_destructive_phase_before_checkout_reset() -> None:
    text = UPDATE.read_text(encoding="utf-8")

    source_index = text.index('source "${SCRIPT_DIR}/update-service-recovery.sh"')
    capture_index = text.index("haxlab_capture_update_service_state")
    function_index = text.index("haxlab_apply_verified_main_update() {")
    stop_index = text.index('systemctl stop "${HAXLAB_UPDATE_MANAGED_SERVICES[@]}"')
    reset_index = text.index('git -C "${APP_DIR}" reset --hard origin/main')
    restart_index = text.index(
        "systemctl start haxlab-ingest.service haxlab-worker.service "
        "haxlab-analyzer.service haxlab-autonomy.timer"
    )
    status_index = text.index("\n  haxlab-status\n", function_index)
    disable_index = text.index(
        "\n  haxlab_disable_update_recovery_trap\n",
        function_index,
    )
    function_end_index = text.index(
        "\n}\nhaxlab_install_update_recovery_trap\n",
        disable_index,
    )
    trap_index = text.index(
        "haxlab_install_update_recovery_trap",
        function_end_index,
    )
    call = "\nhaxlab_apply_verified_main_update\n"
    call_index = text.index(call, trap_index)

    assert source_index < capture_index < function_index
    assert function_index < stop_index < reset_index < restart_index
    assert restart_index < status_index < disable_index < function_end_index
    assert function_end_index < trap_index < call_index
    assert text[call_index + len(call) :].strip() == ""


def test_all_managed_services_restore_in_exact_reverse_stop_order(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    active = (
        "haxlab-live-bot.service",
        "haxlab-autonomy.timer",
        "haxlab-autonomy.service",
        "haxlab-analyzer.service",
        "haxlab-worker.service",
        "haxlab-ingest.service",
    )
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(active)}
haxlab_capture_update_service_state
haxlab_restore_update_services
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == list(reversed(active))


def test_disabling_recovery_trap_prevents_later_failure_restore(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
{_mock_systemctl(("haxlab-worker.service", "haxlab-ingest.service"))}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
haxlab_disable_update_recovery_trap
exit 31
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 31
    assert not calls.exists()


def test_late_failure_restores_exact_preupdate_activation_state(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "start-calls"
    stop_calls = tmp_path / "stop-calls"
    active = ("haxlab-worker.service", "haxlab-ingest.service")
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
STOP_CALLS="$2"
{_mock_systemctl(active)}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 41
"""

    result = _run_bash(script, str(calls), str(stop_calls))

    assert result.returncode == 41
    assert stop_calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-live-bot.service",
        "haxlab-autonomy.timer",
        "haxlab-autonomy.service",
        "haxlab-analyzer.service",
    ]
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-worker.service",
    ]


def test_recovery_continues_after_stop_and_start_failures(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "start-calls"
    stop_calls = tmp_path / "stop-calls"
    active = (
        "haxlab-live-bot.service",
        "haxlab-analyzer.service",
        "haxlab-ingest.service",
    )
    script = f"""
set -euo pipefail
source {shlex.quote(str(HELPER))}
CALLS="$1"
STOP_CALLS="$2"
{_mock_systemctl(
    active,
    fail_start="haxlab-analyzer.service",
    fail_stop="haxlab-autonomy.timer",
)}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 53
"""

    result = _run_bash(script, str(calls), str(stop_calls))

    assert result.returncode == 53
    assert stop_calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-autonomy.timer",
        "haxlab-autonomy.service",
        "haxlab-worker.service",
    ]
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-analyzer.service",
        "haxlab-live-bot.service",
    ]
