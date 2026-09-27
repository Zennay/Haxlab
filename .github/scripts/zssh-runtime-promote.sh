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

echo "::group::Require or safely refresh prechange"
PRE_JSON="$(mktemp /tmp/zssh-prechange.XXXXXX.json)"
LIVE_CONFIG_JSON="$(mktemp /tmp/zssh-live-config.XXXXXX.json)"
trap 'rm -rf "$CANDIDATE" "$POST_CAPTURE" "$POST_WRAPPER" "$PRE_JSON" "$LIVE_CONFIG_JSON"' EXIT

if python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py" --root "$LIVE_ROOT" --state "$STATE_DIR" --json >"$PRE_JSON"; then
  echo "PRECHANGE_ALREADY_GREEN"
else
  cat "$PRE_JSON"
  python3 - "$PRE_JSON" <<'PY'
import json, sys
payload=json.load(open(sys.argv[1]))
unexpected=set(payload.get("unexpected_changes") or [])
assert unexpected == {"resource-policy.json"}, f"unexpected live drift: {sorted(unexpected)}"
checks={x.get("name"):x for x in payload.get("checks") or []}
for name in ("lkg_integrity","firefox_source_runtime_match","zcloud_service","firefox_service","zcloud_http"):
    assert checks.get(name,{}).get("ok") is True, f"prechange safety check failed: {name}"
print("RESOURCE_POLICY_ONLY_DRIFT_VERIFIED")
PY

  python3 "$CANDIDATE/scripts/zcloud_config_validate.py" --projects "$LIVE_ROOT/projects.json" --layout "$LIVE_ROOT/project-layout.json" --resource-policy "$LIVE_ROOT/resource-policy.json" --server "$LIVE_ROOT/server.py" --enhancements "$LIVE_ROOT/enhancements.py" --db "$LIVE_ROOT/history.db" --json >"$LIVE_CONFIG_JSON"

  python3 - "$LIVE_CONFIG_JSON" <<'PY'
import json, sys
payload=json.load(open(sys.argv[1]))
assert payload.get("ok") is True, payload
print("LIVE_CONFIG_SCHEMA_GREEN")
PY

  python3 "$CANDIDATE/scripts/zcloud_postdeploy_canary.py" --root "$LIVE_ROOT" --db "$LIVE_ROOT/history.db" --require-incidents

  python3 "$CANDIDATE/scripts/zcloud_recovery.py" --root "$LIVE_ROOT" --state-dir "$STATE_DIR" capture --evidence "GitHub zSSH registration preflight: only mutable resource-policy drift; live config and postdeploy canary green"

  python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py" --root "$LIVE_ROOT" --state "$STATE_DIR"
fi
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
