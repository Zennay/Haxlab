#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/update-vps.sh"
  exit 1
fi

systemctl stop haxlab-analyzer.service haxlab-worker.service haxlab-ingest.service 2>/dev/null || true

apt-get install -y nodejs npm

git -C "${APP_DIR}" fetch --prune origin
git -C "${APP_DIR}" reset --hard origin/main
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m compileall -q "${APP_DIR}/src/haxlab"

cd "${APP_DIR}"
npm install --omit=dev --no-audit --no-fund
node -e 'const api=require("node-haxball")(); if (!api.Replay) process.exit(1)'

install -m 0644 "${APP_DIR}/deploy/haxlab-ingest.service" /etc/systemd/system/haxlab-ingest.service
install -m 0644 "${APP_DIR}/deploy/haxlab-worker.service" /etc/systemd/system/haxlab-worker.service
install -m 0644 "${APP_DIR}/deploy/haxlab-analyzer.service" /etc/systemd/system/haxlab-analyzer.service

ln -sf "${APP_DIR}/.venv/bin/haxlab" /usr/local/bin/haxlab
ln -sf "${APP_DIR}/.venv/bin/haxlab-status" /usr/local/bin/haxlab-status
ln -sf "${APP_DIR}/.venv/bin/haxlab-worker" /usr/local/bin/haxlab-worker
ln -sf "${APP_DIR}/.venv/bin/haxlab-daemon" /usr/local/bin/haxlab-daemon
ln -sf "${APP_DIR}/.venv/bin/haxlab-analyzer" /usr/local/bin/haxlab-analyzer

systemctl daemon-reload
systemctl enable haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service
systemctl start haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service

systemctl is-active --quiet haxlab-ingest.service
systemctl is-active --quiet haxlab-worker.service
systemctl is-active --quiet haxlab-analyzer.service

haxlab-status
