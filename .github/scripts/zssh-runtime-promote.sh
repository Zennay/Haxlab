#!/usr/bin/env bash
set -Eeuo pipefail

: "${ZSSH_CANONICAL_SHA:=b1363aee962d5d67f38352c4169166b7c52c2bca}"
test "$(id -un)" = "ubuntu"

LIVE_ROOT="/home/ubuntu/zennay-cloud"
STATE_DIR="/home/ubuntu/.local/state/zcloud/recovery"
CANDIDATE="$(mktemp -d /tmp/zssh-repair.XXXXXX)"
PRE_WRAPPER="$(mktemp /tmp/zssh-prechange-wrapper.XXXXXX)"
POST_WRAPPER="$(mktemp /tmp/zssh-postdeploy-wrapper.XXXXXX)"
POST_CAPTURE="$(mktemp /tmp/zssh-postdeploy.XXXXXX.json)"
VALIDATE_JSON="$(mktemp /tmp/zssh-validate.XXXXXX.json)"
trap 'rm -rf "$CANDIDATE" "$PRE_WRAPPER" "$POST_WRAPPER" "$POST_CAPTURE" "$VALIDATE_JSON"' EXIT

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

echo "::group::Verify live zSSH runtime"
curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active --quiet zssh.service
test "$(readlink -f /home/ubuntu/.local/share/zssh/current)" = "/home/ubuntu/.local/share/zssh/releases/$ZSSH_CANONICAL_SHA"
echo "ZSSH_RUNTIME_GREEN"
echo "::endgroup::"

echo "::group::Fetch exact canonical zCloud revision"
git clone --filter=blob:none --no-checkout https://github.com/Zennay/zCloud.git "$CANDIDATE"
git -C "$CANDIDATE" fetch --depth 1 origin "$ZSSH_CANONICAL_SHA"
git -C "$CANDIDATE" checkout --detach "$ZSSH_CANONICAL_SHA"
test "$(git -C "$CANDIDATE" rev-parse HEAD)" = "$ZSSH_CANONICAL_SHA"
echo "::endgroup::"

echo "::group::Prove current inconsistency is exactly zSSH registration"
set +e
python3 "$CANDIDATE/scripts/zcloud_config_validate.py"   --projects "$LIVE_ROOT/projects.json"   --layout "$LIVE_ROOT/project-layout.json"   --resource-policy "$LIVE_ROOT/resource-policy.json"   --server "$LIVE_ROOT/server.py"   --enhancements "$LIVE_ROOT/enhancements.py"   --db "$LIVE_ROOT/history.db"   --json >"$VALIDATE_JSON"
current_rc=$?
set -e
cat "$VALIDATE_JSON"
python3 - "$VALIDATE_JSON" <<'PY'
import json, sys
p=json.load(open(sys.argv[1]))
errors=set(p.get("errors") or [])
expected={"history.db: unknown worker project 'zssh'","history.db: unknown runner target 'zssh'"}
assert p.get("ok") is False, p
assert errors == expected, f"unexpected current config errors: {sorted(errors)}"
print("ZSSH_REGISTRATION_INCONSISTENCY_VERIFIED")
PY
test "$current_rc" -ne 0
echo "::endgroup::"

cat > "$PRE_WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$CANDIDATE/scripts/zcloud_prechange_guard.py" "\$@" --allow-change resource-policy.json
EOF
chmod 700 "$PRE_WRAPPER"

cat > "$POST_WRAPPER" <<EOF
#!/usr/bin/env bash
set +e
"$CANDIDATE/scripts/zcloud_postdeploy_canary.py" "\$@" > "$POST_CAPTURE"
rc=\$?
cat "$POST_CAPTURE"
exit \$rc
EOF
chmod 700 "$POST_WRAPPER"

echo "::group::Strict prechange with only resource-policy exception"
"$PRE_WRAPPER" --root "$LIVE_ROOT" --state "$STATE_DIR"
echo "::endgroup::"

echo "::group::Dry-run registration repair"
python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"   --candidate "$CANDIDATE"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --path projects.json   --path project-layout.json   --actor github-zssh-registration-repair   --require-incidents   --prechange "$PRE_WRAPPER"   --postdeploy "$POST_WRAPPER"   --dry-run
echo "::endgroup::"

echo "::group::Transactional registration repair"
set +e
python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"   --candidate "$CANDIDATE"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --path projects.json   --path project-layout.json   --actor github-zssh-registration-repair   --require-incidents   --prechange "$PRE_WRAPPER"   --postdeploy "$POST_WRAPPER"
rc=$?
set -e
if [[ "$rc" -ne 0 ]]; then
  echo "--- captured postdeploy ---"
  cat "$POST_CAPTURE" || true
  exit "$rc"
fi
echo "::endgroup::"

echo "::group::Final live proof"
cat "$POST_CAPTURE"

python3 "$CANDIDATE/scripts/zcloud_config_validate.py"   --projects "$LIVE_ROOT/projects.json"   --layout "$LIVE_ROOT/project-layout.json"   --resource-policy "$LIVE_ROOT/resource-policy.json"   --server "$LIVE_ROOT/server.py"   --enhancements "$LIVE_ROOT/enhancements.py"   --db "$LIVE_ROOT/history.db"

curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active --quiet zssh.service

python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=5) as response:
    data=json.load(response)
projects=data.get("projects") or []
match=[p for p in projects if p.get("id")=="zssh"]
assert match, "zssh missing from live zCloud status"
print("ZCLOUD_ZSSH_CARD_GREEN", match[0].get("progress"))
PY

python3 "$CANDIDATE/scripts/zcloud_postdeploy_canary.py"   --root "$LIVE_ROOT"   --db "$LIVE_ROOT/history.db"   --require-incidents

echo "ZSSH_GITHUB_PROMOTION_GREEN"
echo "::endgroup::"
