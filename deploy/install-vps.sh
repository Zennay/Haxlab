#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HAXLAB_REPO_URL:-https://github.com/Zennay/Haxlab.git}"
APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
DATA_DIR="${HAXLAB_DATA_DIR:-/var/lib/haxlab}"
SERVICE_USER="${HAXLAB_USER:-haxlab}"
UPLOAD_USER="${HAXLAB_UPLOAD_USER:-${SUDO_USER:-ubuntu}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install-vps.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y git python3 python3-venv python3-pip rsync sqlite3 acl

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p   "${APP_DIR}"   "${DATA_DIR}/incoming"   "${DATA_DIR}/raw/replays"   "${DATA_DIR}/derived"   "${DATA_DIR}/models/challengers"   "${DATA_DIR}/models/champions"   "${DATA_DIR}/state"   "${DATA_DIR}/logs"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  find "${APP_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
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

# The service owns the data tree, while the SSH upload user can keep dropping
# new replay batches into incoming/ without making the whole directory world-writable.
chmod 2770 "${DATA_DIR}/incoming"
if id "${UPLOAD_USER}" >/dev/null 2>&1; then
  setfacl -m "u:${UPLOAD_USER}:rwx" "${DATA_DIR}/incoming"
  setfacl -d -m "u:${UPLOAD_USER}:rwx" "${DATA_DIR}/incoming"
  echo "Upload access granted to: ${UPLOAD_USER}"
fi

install -m 0644 "${APP_DIR}/deploy/haxlab-ingest.service" /etc/systemd/system/haxlab-ingest.service
install -m 0644 "${APP_DIR}/deploy/haxlab-worker.service" /etc/systemd/system/haxlab-worker.service

systemctl daemon-reload
systemctl enable --now haxlab-ingest.service
systemctl enable --now haxlab-worker.service

echo
echo "HaxLab installed."
echo "Upload replays to: ${DATA_DIR}/incoming/"
echo "Status: haxlab-status"
echo "Logs: journalctl -u haxlab-ingest -u haxlab-worker -f"
