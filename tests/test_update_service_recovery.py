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
) -> str:
    active_case = "|".join(active_units) or "__never__"
    failure = (
        f'if [[ "$2" == {shlex.quote(fail_start)} ]]; then return 1; fi'
        if fail_start is not None
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
  if [[ "$1" == "start" ]]; then
    printf '%s\\n' "$2" >> "$CALLS"
    {failure}
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
{_mock_systemctl(("haxlab-analyzer.service", "haxlab-ingest.service"), fail_start="haxlab-analyzer.service")}
haxlab_capture_update_service_state
haxlab_install_update_recovery_trap
exit 17
"""

    result = _run_bash(script, str(calls))

    assert result.returncode == 17
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "haxlab-ingest.service",
        "haxlab-analyzer.service",
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


def test_update_wires_recovery_before_stop_and_disables_after_health_checks() -> None:
    text = UPDATE.read_text(encoding="utf-8")

    source_index = text.index('source "${SCRIPT_DIR}/update-service-recovery.sh"')
    capture_index = text.index("haxlab_capture_update_service_state")
    trap_index = text.index("haxlab_install_update_recovery_trap")
    stop_index = text.index('systemctl stop "${HAXLAB_UPDATE_MANAGED_SERVICES[@]}"')
    status_index = text.index("\nhaxlab-status\n")
    disable_index = text.index("\nhaxlab_disable_update_recovery_trap\n")
    restart_index = text.index(
        "systemctl start haxlab-ingest.service haxlab-worker.service "
        "haxlab-analyzer.service haxlab-autonomy.timer"
    )

    assert source_index < capture_index < trap_index < stop_index
    assert stop_index < restart_index < status_index < disable_index
