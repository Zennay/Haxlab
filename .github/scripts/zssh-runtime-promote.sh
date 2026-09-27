#!/usr/bin/env bash
set -Eeuo pipefail

: "${ZSSH_CANONICAL_SHA:=b1363aee962d5d67f38352c4169166b7c52c2bca}"
test "$(id -un)" = "ubuntu"

LIVE_ROOT="/home/ubuntu/zennay-cloud"
STATE_DIR="/home/ubuntu/.local/state/zcloud/recovery"
CANDIDATE="$(mktemp -d /tmp/zssh-diag.XXXXXX)"
trap 'rm -rf "$CANDIDATE"' EXIT

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

echo "=== zSSH runtime ==="
curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active zssh.service
readlink -f /home/ubuntu/.local/share/zssh/current

git clone --filter=blob:none --no-checkout https://github.com/Zennay/zCloud.git "$CANDIDATE"
git -C "$CANDIDATE" fetch --depth 1 origin "$ZSSH_CANONICAL_SHA"
git -C "$CANDIDATE" checkout --detach "$ZSSH_CANONICAL_SHA"

echo "=== prechange ==="
python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py" --root "$LIVE_ROOT" --state "$STATE_DIR" --json || true

echo "=== live config validator ==="
python3 "$CANDIDATE/scripts/zcloud_config_validate.py"   --projects "$LIVE_ROOT/projects.json"   --layout "$LIVE_ROOT/project-layout.json"   --resource-policy "$LIVE_ROOT/resource-policy.json"   --server "$LIVE_ROOT/server.py"   --enhancements "$LIVE_ROOT/enhancements.py"   --db "$LIVE_ROOT/history.db"   --json || true

echo "=== live resource policy ==="
cat "$LIVE_ROOT/resource-policy.json"

echo "=== current postdeploy ==="
python3 "$CANDIDATE/scripts/zcloud_postdeploy_canary.py"   --root "$LIVE_ROOT"   --db "$LIVE_ROOT/history.db"   --require-incidents   --json || true

echo "ZSSH_REGISTRATION_DIAG_COMPLETE"
