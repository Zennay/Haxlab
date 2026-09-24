#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/update-vps.sh"
  exit 1
fi

systemctl stop haxlab-worker.service haxlab-ingest.service

git -C "${APP_DIR}" fetch --prune origin
git -C "${APP_DIR}" reset --hard origin/main
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"

install -m 0644 "${APP_DIR}/deploy/haxlab-ingest.service" /etc/systemd/system/haxlab-ingest.service
install -m 0644 "${APP_DIR}/deploy/haxlab-worker.service" /etc/systemd/system/haxlab-worker.service

systemctl daemon-reload
systemctl start haxlab-ingest.service haxlab-worker.service

"${APP_DIR}/.venv/bin/python" -m haxlab.runtime.status
