#!/usr/bin/env bash

HAXLAB_UPDATE_MANAGED_SERVICES=(
  haxlab-live-bot.service
  haxlab-autonomy.timer
  haxlab-autonomy.service
  haxlab-analyzer.service
  haxlab-worker.service
  haxlab-ingest.service
)

HAXLAB_UPDATE_ACTIVE_BEFORE=()

haxlab_capture_update_service_state() {
  HAXLAB_UPDATE_ACTIVE_BEFORE=()

  local unit
  for unit in "${HAXLAB_UPDATE_MANAGED_SERVICES[@]}"; do
    if systemctl is-active --quiet "${unit}" 2>/dev/null; then
      HAXLAB_UPDATE_ACTIVE_BEFORE+=("${unit}")
    fi
  done
}

haxlab_update_service_was_active() {
  local expected="${1}"
  local unit

  for unit in "${HAXLAB_UPDATE_ACTIVE_BEFORE[@]}"; do
    if [[ "${unit}" == "${expected}" ]]; then
      return 0
    fi
  done
  return 1
}

haxlab_restore_update_services() {
  local failed=0
  local index
  local unit

  for unit in "${HAXLAB_UPDATE_MANAGED_SERVICES[@]}"; do
    if haxlab_update_service_was_active "${unit}"; then
      continue
    fi
    if ! systemctl stop "${unit}"; then
      failed=1
    fi
  done

  for (( index=${#HAXLAB_UPDATE_ACTIVE_BEFORE[@]} - 1; index >= 0; index-- )); do
    unit="${HAXLAB_UPDATE_ACTIVE_BEFORE[index]}"
    if ! systemctl start "${unit}"; then
      failed=1
    fi
  done

  return "${failed}"
}

_haxlab_update_exit_recovery() {
  local status="${?}"
  trap - EXIT

  if (( status != 0 )); then
    set +e
    haxlab_restore_update_services || true
  fi

  exit "${status}"
}

haxlab_install_update_recovery_trap() {
  trap '_haxlab_update_exit_recovery' EXIT
}

haxlab_disable_update_recovery_trap() {
  trap - EXIT
}
