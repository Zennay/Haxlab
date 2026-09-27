#!/usr/bin/env bash
set -Eeuo pipefail

: "${ZSSH_CANONICAL_SHA:=b1363aee962d5d67f38352c4169166b7c52c2bca}"
test "$(id -un)" = "ubuntu"

LIVE_ROOT="/home/ubuntu/zennay-cloud"
STATE_DIR="/home/ubuntu/.local/state/zcloud/recovery"
CANDIDATE="$(mktemp -d /tmp/zssh-final.XXXXXX)"
PRE_JSON="$(mktemp /tmp/zssh-prechange.XXXXXX.json)"
LIVE_CONFIG_JSON="$(mktemp /tmp/zssh-live-config.XXXXXX.json)"
trap 'rm -rf "$CANDIDATE" "$PRE_JSON" "$LIVE_CONFIG_JSON"' EXIT

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
test -S "$XDG_RUNTIME_DIR/bus"

echo "::group::Fetch exact canonical zCloud revision"
git clone --filter=blob:none --no-checkout https://github.com/Zennay/zCloud.git "$CANDIDATE"
git -C "$CANDIDATE" fetch --depth 1 origin "$ZSSH_CANONICAL_SHA"
git -C "$CANDIDATE" checkout --detach "$ZSSH_CANONICAL_SHA"
test "$(git -C "$CANDIDATE" rev-parse HEAD)" = "$ZSSH_CANONICAL_SHA"
echo "::endgroup::"

echo "::group::Re-verify canonical zSSH before live writes"
npm install --prefix "$CANDIDATE/zssh" --ignore-scripts --no-audit --no-fund
npm test --prefix "$CANDIDATE/zssh"
echo "::endgroup::"

echo "::group::Validate current zCloud baseline"
if python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --json >"$PRE_JSON"; then
  echo "PRECHANGE_ALREADY_GREEN"
else
  cat "$PRE_JSON"
  python3 - "$PRE_JSON" <<'PY'
import json, sys
payload=json.load(open(sys.argv[1]))
unexpected=set(payload.get("unexpected_changes") or [])
expected={"enhancements.py","resource-policy.json"}
assert unexpected == expected, f"unexpected live drift: {sorted(unexpected)}"
checks={x.get("name"):x for x in payload.get("checks") or []}
for name in ("lkg_integrity","firefox_source_runtime_match","zcloud_service","firefox_service","zcloud_http"):
    assert checks.get(name,{}).get("ok") is True, f"prechange safety check failed: {name}"
print("STALE_LKG_DRIFT_SHAPE_VERIFIED")
PY

  cmp "$CANDIDATE/enhancements.py" "$LIVE_ROOT/enhancements.py"
  echo "ENHANCEMENTS_CANONICAL_GREEN"

  python3 "$CANDIDATE/scripts/zcloud_config_validate.py"     --projects "$LIVE_ROOT/projects.json"     --layout "$LIVE_ROOT/project-layout.json"     --resource-policy "$LIVE_ROOT/resource-policy.json"     --server "$LIVE_ROOT/server.py"     --enhancements "$LIVE_ROOT/enhancements.py"     --db "$LIVE_ROOT/history.db"     --json >"$LIVE_CONFIG_JSON"

  python3 - "$LIVE_CONFIG_JSON" <<'PY'
import json, sys
payload=json.load(open(sys.argv[1]))
assert payload.get("ok") is True, payload
print("LIVE_CONFIG_SCHEMA_GREEN")
PY

  python3 "$CANDIDATE/scripts/zcloud_postdeploy_canary.py"     --root "$LIVE_ROOT"     --db "$LIVE_ROOT/history.db"     --require-incidents

  python3 "$CANDIDATE/scripts/zcloud_recovery.py"     --root "$LIVE_ROOT"     --state-dir "$STATE_DIR"     capture     --evidence "GitHub zSSH preflight: stale LKG refreshed only after exact drift verification; enhancements.py equals canonical; live resource-policy operator priorities preserved; config/services/API/browser/incidents green"

  python3 "$CANDIDATE/scripts/zcloud_prechange_guard.py"     --root "$LIVE_ROOT"     --state "$STATE_DIR"
fi
echo "::endgroup::"

echo "::group::Dry-run zCloud project registration"
python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"   --candidate "$CANDIDATE"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --path projects.json   --path project-layout.json   --actor github-zssh-runtime-promote   --require-incidents   --dry-run
echo "::endgroup::"

echo "::group::Install canonical zSSH user service"
HOME=/home/ubuntu ZSSH_EXPECTED_SHA="$ZSSH_CANONICAL_SHA" bash "$CANDIDATE/zssh/deploy/install-live.sh" "$CANDIDATE"
echo "::endgroup::"

echo "::group::Promote zCloud registration transactionally"
python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"   --candidate "$CANDIDATE"   --root "$LIVE_ROOT"   --state "$STATE_DIR"   --path projects.json   --path project-layout.json   --actor github-zssh-runtime-promote   --require-incidents
echo "::endgroup::"

echo "::group::Final live proof"
curl -fsS http://127.0.0.1:8788/health
systemctl --user is-active --quiet zssh.service

card_green=0
for _ in $(seq 1 90); do
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

python3 "$CANDIDATE/scripts/zcloud_postdeploy_canary.py"   --root "$LIVE_ROOT"   --db "$LIVE_ROOT/history.db"   --require-incidents

echo "ZSSH_GITHUB_PROMOTION_GREEN"
echo "::endgroup::"
