#!/usr/bin/env bash
set -Eeuo pipefail

: "${ZSSH_CANONICAL_SHA:=b1363aee962d5d67f38352c4169166b7c52c2bca}"
test "$(id -un)" = "ubuntu"

LIVE_ROOT="/home/ubuntu/zennay-cloud"
STATE_DIR="/home/ubuntu/.local/state/zcloud/recovery"
CANDIDATE="$(mktemp -d /tmp/zssh-register.XXXXXX)"
POST_CAPTURE="$(mktemp /tmp/zssh-postdeploy.XXXXXX.json)"
POST_WRAPPER="$(mktemp /tmp/zssh-postdeploy-wrapper.XXXXXX)"
trap 'rm -rf "$CANDIDATE" "$POST_CAPTURE" "$POST_WRAPPER"' EXIT

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
test -S "$XDG_RUNTIME_DIR/bus"

echo "::group::Verify live zSSH runtime"
curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active --quiet zssh.service
CURRENT="$(readlink -f /home/ubuntu/.local/share/zssh/current)"
test "$CURRENT" = "/home/ubuntu/.local/share/zssh/releases/$ZSSH_CANONICAL_SHA"
echo "ZSSH_RUNTIME_GREEN $CURRENT"
echo "::endgroup::"

echo "::group::Fetch exact canonical zCloud revision"
git clone --filter=blob:none --no-checkout https://github.com/Zennay/zCloud.git "$CANDIDATE"
git -C "$CANDIDATE" fetch --depth 1 origin "$ZSSH_CANONICAL_SHA"
git -C "$CANDIDATE" checkout --detach "$ZSSH_CANONICAL_SHA"
test "$(git -C "$CANDIDATE" rev-parse HEAD)" = "$ZSSH_CANONICAL_SHA"
echo "::endgroup::"

echo "::group::Require clean prechange"
python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py"   --root "$LIVE_ROOT"   --state "$STATE_DIR"
echo "::endgroup::"

cat > "$POST_WRAPPER" <<EOF
#!/usr/bin/env bash
set +e
"$CANDIDATE/scripts/zcloud_postdeploy_canary.py" "\$@" > "$POST_CAPTURE"
rc=\$?
cat "$POST_CAPTURE"
exit \$rc
EOF
chmod 700 "$POST_WRAPPER"

echo "::group::Promote zCloud registration with captured postdeploy"
set +e
python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"   --candidate "$CANDIDATE"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --path projects.json   --path project-layout.json   --actor github-zssh-runtime-promote   --require-incidents   --postdeploy "$POST_WRAPPER"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  echo "--- captured failing postdeploy payload ---"
  cat "$POST_CAPTURE" || true
  echo "--- rollback state ---"
  python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py"     --root "$LIVE_ROOT"     --state "$STATE_DIR" || true
  exit "$rc"
fi
echo "::endgroup::"

echo "::group::Final live proof"
cat "$POST_CAPTURE"
curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active --quiet zssh.service

card_green=0
for _ in $(seq 1 60); do
  if python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=5) as response:
    data=json.load(response)
projects=data.get("projects") or []
match=[p for p in projects if p.get("id")=="zssh"]
assert match, "zssh missing from live zCloud status"
print("ZCLOUD_ZSSH_CARD_GREEN", match[0].get("progress"))
PY
  then
    card_green=1
    break
  fi
  sleep 1
done
[[ "$card_green" = "1" ]]

echo "ZSSH_GITHUB_PROMOTION_GREEN"
echo "::endgroup::"
