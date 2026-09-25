#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/update-vps.sh"
  exit 1
fi

training_state="$(systemctl show haxlab-training-shards.service -p ActiveState --value 2>/dev/null || true)"
training_was_running=0
if [[ "${training_state}" == "active" || "${training_state}" == "activating" ]]; then
  training_was_running=1
  echo "Pausing resumable imitation shard materialization for deploy..."
  systemctl stop haxlab-training-shards.service 2>/dev/null || true
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
install -m 0644 "${APP_DIR}/deploy/haxlab-training-shards.service" /etc/systemd/system/haxlab-training-shards.service

ln -sf "${APP_DIR}/.venv/bin/haxlab" /usr/local/bin/haxlab
ln -sf "${APP_DIR}/.venv/bin/haxlab-status" /usr/local/bin/haxlab-status
ln -sf "${APP_DIR}/.venv/bin/haxlab-worker" /usr/local/bin/haxlab-worker
ln -sf "${APP_DIR}/.venv/bin/haxlab-daemon" /usr/local/bin/haxlab-daemon
ln -sf "${APP_DIR}/.venv/bin/haxlab-analyzer" /usr/local/bin/haxlab-analyzer
ln -sf "${APP_DIR}/.venv/bin/haxlab-players" /usr/local/bin/haxlab-players
ln -sf "${APP_DIR}/.venv/bin/haxlab-skill" /usr/local/bin/haxlab-skill
ln -sf "${APP_DIR}/.venv/bin/haxlab-training-manifest" /usr/local/bin/haxlab-training-manifest
ln -sf "${APP_DIR}/.venv/bin/haxlab-build-shards" /usr/local/bin/haxlab-build-shards
ln -sf "${APP_DIR}/.venv/bin/haxlab-materialize-shards" /usr/local/bin/haxlab-materialize-shards
ln -sf "${APP_DIR}/.venv/bin/haxlab-train-bc" /usr/local/bin/haxlab-train-bc
install -o root -g root -m 0755 "${APP_DIR}/deploy/haxlab-actions-control.sh" /usr/local/sbin/haxlab-actions-control

systemctl daemon-reload
systemctl enable haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service
systemctl start haxlab-ingest.service haxlab-worker.service haxlab-analyzer.service

if [[ "${training_was_running}" -eq 1 ]]; then
  echo "Resuming imitation shard materialization from cache..."
  systemctl start haxlab-training-shards.service
fi

systemctl is-active --quiet haxlab-ingest.service
systemctl is-active --quiet haxlab-worker.service
systemctl is-active --quiet haxlab-analyzer.service

haxlab-status
