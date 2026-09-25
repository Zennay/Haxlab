#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HAXLAB_GITHUB_REPO_URL:-https://github.com/Zennay/Haxlab}"
RUNNER_DIR="${HAXLAB_RUNNER_DIR:-/opt/actions-runner-haxlab}"
RUNNER_USER="${HAXLAB_RUNNER_USER:-${SUDO_USER:-ubuntu}}"
RUNNER_NAME="${HAXLAB_RUNNER_NAME:-$(hostname)-haxlab}"
RUNNER_LABELS="${HAXLAB_RUNNER_LABELS:-haxlab}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo and pass the short-lived GitHub runner token:"
  echo "  sudo HAXLAB_RUNNER_TOKEN=... bash deploy/install-github-runner.sh"
  exit 1
fi

if [[ -z "${HAXLAB_RUNNER_TOKEN:-}" ]]; then
  echo "HAXLAB_RUNNER_TOKEN is required." >&2
  echo "GitHub: Settings -> Actions -> Runners -> New self-hosted runner" >&2
  exit 2
fi

apt-get update
apt-get install -y curl jq tar

arch="$(uname -m)"
case "${arch}" in
  x86_64) runner_arch="x64" ;;
  aarch64|arm64) runner_arch="arm64" ;;
  *)
    echo "Unsupported architecture: ${arch}" >&2
    exit 3
    ;;
esac

release_json="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
runner_version="$(jq -r '.tag_name | ltrimstr("v")' <<<"${release_json}")"
runner_url="https://github.com/actions/runner/releases/download/v${runner_version}/actions-runner-linux-${runner_arch}-${runner_version}.tar.gz"

mkdir -p "${RUNNER_DIR}"
chown "${RUNNER_USER}:${RUNNER_USER}" "${RUNNER_DIR}"

if [[ ! -x "${RUNNER_DIR}/config.sh" ]]; then
  tmp="$(mktemp)"
  curl -fsSL "${runner_url}" -o "${tmp}"
  tar -xzf "${tmp}" -C "${RUNNER_DIR}"
  rm -f "${tmp}"
  chown -R "${RUNNER_USER}:${RUNNER_USER}" "${RUNNER_DIR}"
fi

if [[ -f "${RUNNER_DIR}/.runner" ]]; then
  echo "Runner is already configured at ${RUNNER_DIR}."
else
  runuser -u "${RUNNER_USER}" -- "${RUNNER_DIR}/config.sh" \
    --unattended \
    --url "${REPO_URL}" \
    --token "${HAXLAB_RUNNER_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${RUNNER_LABELS}" \
    --work "_work"
fi

cd "${RUNNER_DIR}"
./svc.sh install "${RUNNER_USER}" 2>/dev/null || true
./svc.sh start

install -o root -g root -m 0755   /opt/haxlab/deploy/haxlab-actions-control.sh   /usr/local/sbin/haxlab-actions-control

cat >/etc/sudoers.d/haxlab-actions <<EOF
${RUNNER_USER} ALL=(root) NOPASSWD: /usr/local/sbin/haxlab-actions-control *
EOF
chmod 0440 /etc/sudoers.d/haxlab-actions
visudo -cf /etc/sudoers.d/haxlab-actions

echo
echo "HaxLab GitHub runner installed."
echo "Runner: ${RUNNER_NAME}"
echo "Labels: self-hosted, linux, ${runner_arch}, ${RUNNER_LABELS}"
echo
echo "GitHub Actions can now deploy/status/restart HaxLab without SSH."
