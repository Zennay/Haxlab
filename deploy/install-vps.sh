#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HAXLAB_REPO_URL:-https://github.com/Zennay/Haxlab.git}"
APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
DATA_DIR="${HAXLAB_DATA_DIR:-/var/lib/haxlab}"
SERVICE_USER="${HAXLAB_USER:-haxlab}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install-vps.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y git python3 python3-venv python3-pip rsync sqlite3

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p   "${APP_DIR}"   "${DATA_DIR}/incoming"   "${DATA_DIR}/raw/replays"   "${DATA_DIR}/derived"   "${DATA_DIR}/models/challengers"   "${DATA_DIR}/models/champions"   "${DATA_DIR}/state"   "${DATA_DIR}/logs"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  rm -rf "${APP_DIR:?}/"*
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch --prune origin
  git -C "${APP_DIR}" reset --hard origin/main
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"
chown -R root:root "${APP_DIR}"
chmod 755 "${DATA_DIR}/incoming"

install -m 0644 "${APP_DIR}/deploy/haxlab-ingest.service" /etc/systemd/system/haxlab-ingest.service
install -m 0644 "${APP_DIR}/deploy/haxlab-worker.service" /etc/systemd/system/haxlab-worker.service

systemctl daemon-reload
systemctl enable --now haxlab-ingest.service
systemctl enable --now haxlab-worker.service

echo
echo "HaxLab installed."
echo "Upload replays to: ${DATA_DIR}/incoming/"
echo "Status: ${APP_DIR}/.venv/bin/python -m haxlab.runtime.status"
echo "Logs: journalctl -u haxlab-ingest -u haxlab-worker -f"
