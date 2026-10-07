#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/update-vps.sh"
  exit 1
fi

source "${SCRIPT_DIR}/update-service-recovery.sh"
haxlab_capture_update_service_state

LIVE_WAS_ACTIVE=0
if haxlab_update_service_was_active haxlab-live-bot.service; then
  LIVE_WAS_ACTIVE=1
fi

haxlab_install_update_recovery_trap
systemctl stop "${HAXLAB_UPDATE_MANAGED_SERVICES[@]}" 2>/dev/null || true

apt-get install -y nodejs npm

git -C "${APP_DIR}" fetch --prune origin
git -C "${APP_DIR}" reset --hard origin/main
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m compileall -q "${APP_DIR}/src/haxlab"

cd "${APP_DIR}"
npm install --omit=dev --no-audit --no-fund
node -e 'const api=require("node-haxball")(); if (!api.Replay || !api.Room || !api.Utils) process.exit(1)'
node --check "${APP_DIR}/tools/live_haxball_bot.js"

install -m 0644 "${APP_DIR}/deploy/haxlab-ingest.service" /etc/systemd/system/haxlab-ingest.service
install -m 0644 "${APP_DIR}/deploy/haxlab-worker.service" /etc/systemd/system/haxlab-worker.service
install -m 0644 "${APP_DIR}/deploy/haxlab-analyzer.service" /etc/systemd/system/haxlab-analyzer.service
install -m 0644 "${APP_DIR}/deploy/haxlab-autonomy.service" /etc/systemd/system/haxlab-autonomy.service
install -m 0644 "${APP_DIR}/deploy/haxlab-autonomy.timer" /etc/systemd/system/haxlab-autonomy.timer
install -m 0644 "${APP_DIR}/deploy/haxlab-live-bot.service" /etc/systemd/system/haxlab-live-bot.service
chmod 0755 "${APP_DIR}/deploy/haxlab-autonomy-tick.sh"

ln -sf "${APP_DIR}/.venv/bin/haxlab" /usr/local/bin/haxlab
ln -sf "${APP_DIR}/.venv/bin/haxlab-status" /usr/local/bin/haxlab-status
ln -sf "${APP_DIR}/.venv/bin/haxlab-worker" /usr/local/bin/haxlab-worker
ln -sf "${APP_DIR}/.venv/bin/haxlab-daemon" /usr/local/bin/haxlab-daemon
ln -sf "${APP_DIR}/.venv/bin/haxlab-analyzer" /usr/local/bin/haxlab-analyzer
ln -sf "${APP_DIR}/.venv/bin/haxlab-players" /usr/local/bin/haxlab-players
ln -sf "${APP_DIR}/.venv/bin/haxlab-skill" /usr/local/bin/haxlab-skill
ln -sf "${APP_DIR}/.venv/bin/haxlab-training-manifest" /usr/local/bin/haxlab-training-manifest
ln -sf "${APP_DIR}/.venv/bin/haxlab-build-shards" /usr/local/bin/haxlab-build-shards
ln -sf "${APP_DIR}/.venv/bin/haxlab-train-bc" /usr/local/bin/haxlab-train-bc
ln -sf "${APP_DIR}/.venv/bin/haxlab-generation-loop" /usr/local/bin/haxlab-generation-loop
install -o root -g root -m 0755 "${APP_DIR}/deploy/haxlab-actions-control.sh" /usr/local/sbin/haxlab-actions-control

systemctl daemon-reload
systemctl enable haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service haxlab-autonomy.timer
systemctl start haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service haxlab-autonomy.timer
if [[ "${LIVE_WAS_ACTIVE}" -eq 1 && -f /var/lib/haxlab/state/live-play.env ]]; then
  systemctl start haxlab-live-bot.service
fi

systemctl is-active --quiet haxlab-ingest.service
systemctl is-active --quiet haxlab-worker.service
systemctl is-active --quiet haxlab-analyzer.service
systemctl is-active --quiet haxlab-autonomy.timer

haxlab-status
haxlab_disable_update_recovery_trap
