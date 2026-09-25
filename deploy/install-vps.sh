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

apt_update_with_recovery() {
  if apt-get update; then
    return 0
  fi

  echo
  echo "apt update failed. Checking for known unsupported third-party repositories..."

  mapfile -t broken_x2go_sources < <(
    grep -RIl --include='*.list' --include='*.sources' \
      'ppa.launchpadcontent.net/x2go/stable/ubuntu' \
      /etc/apt/sources.list.d 2>/dev/null || true
  )

  if (( ${#broken_x2go_sources[@]} > 0 )); then
    echo "Disabling unsupported X2Go PPA source(s):"
    for source_file in "${broken_x2go_sources[@]}"; do
      echo "  - ${source_file}"
      mv "${source_file}" "${source_file}.disabled-haxlab"
    done
    echo "Retrying apt update..."
    apt-get update
    return 0
  fi

  echo "No known recoverable source was found."
  echo "Fix the apt repository error above and rerun this installer."
  return 1
}

apt_update_with_recovery
apt-get install -y git python3 python3-venv python3-pip rsync sqlite3 acl nodejs npm

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p \
  "${APP_DIR}" \
  "${DATA_DIR}/incoming" \
  "${DATA_DIR}/raw/replays" \
  "${DATA_DIR}/derived" \
  "${DATA_DIR}/models/challengers" \
  "${DATA_DIR}/models/champions" \
  "${DATA_DIR}/state" \
  "${DATA_DIR}/logs"

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
"${APP_DIR}/.venv/bin/python" -m compileall -q "${APP_DIR}/src/haxlab"

cd "${APP_DIR}"
npm install --omit=dev --no-audit --no-fund
node -e 'const api=require("node-haxball")(); if (!api.Replay) process.exit(1)'

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"
chown -R root:root "${APP_DIR}"

chmod 2770 "${DATA_DIR}/incoming"
if id "${UPLOAD_USER}" >/dev/null 2>&1; then
  setfacl -m "u:${UPLOAD_USER}:rwx" "${DATA_DIR}/incoming"
  setfacl -d -m "u:${UPLOAD_USER}:rwx" "${DATA_DIR}/incoming"
  echo "Upload access granted to: ${UPLOAD_USER}"
fi

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
systemctl enable --now haxlab-ingest.service
systemctl enable --now haxlab-worker.service
systemctl enable --now haxlab-analyzer.service

systemctl is-active --quiet haxlab-ingest.service
systemctl is-active --quiet haxlab-worker.service
systemctl is-active --quiet haxlab-analyzer.service

haxlab-status >/dev/null

echo
echo "HaxLab installed."
echo "Upload replays to: ${DATA_DIR}/incoming/"
echo "Status: haxlab-status"
echo "Full analyzer: systemctl status haxlab-analyzer --no-pager"
echo "Logs: journalctl -u haxlab-ingest -u haxlab-worker -u haxlab-analyzer -f"
