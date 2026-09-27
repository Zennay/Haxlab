#!/usr/bin/env bash
set -Eeuo pipefail

: "${ZSSH_CANONICAL_SHA:=ba3e261a036897970ad9af1e1a2c1bba62efa9af}"

test "$(id -un)" = "ubuntu"

LIVE_ROOT="/home/ubuntu/zennay-cloud"
STATE_DIR="/home/ubuntu/.local/state/zcloud/recovery"
RELEASE_ROOT="/home/ubuntu/.local/share/zssh"
CONFIG_ROOT="/home/ubuntu/.config/zssh"
SERVICE_ROOT="/home/ubuntu/.config/systemd/user"
CANDIDATE="$(mktemp -d /tmp/zssh-candidate.XXXXXX)"
CANARY_FILE="$LIVE_ROOT/.zssh-canary.txt"
ENV_FILE="$CONFIG_ROOT/zssh.env"
OLD_TARGET=""
OLD_ACTIVE=0
RUNTIME_SWITCHED=0

cleanup() {
  rm -rf "$CANDIDATE"
  rm -f "$CANARY_FILE" /tmp/zssh-health.json
}
trap cleanup EXIT

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
test -S "$XDG_RUNTIME_DIR/bus"

rollback_runtime() {
  set +e
  if [[ "$RUNTIME_SWITCHED" != "1" ]]; then
    return 0
  fi
  if [[ -n "$OLD_TARGET" && -d "$OLD_TARGET" ]]; then
    ln -sfn "$OLD_TARGET" "$RELEASE_ROOT/current"
    systemctl --user daemon-reload
    systemctl --user restart zssh.service
  else
    systemctl --user stop zssh.service
    systemctl --user disable zssh.service >/dev/null 2>&1
    rm -f "$RELEASE_ROOT/current"
  fi
  if [[ "$OLD_ACTIVE" != "1" && -n "$OLD_TARGET" ]]; then
    systemctl --user stop zssh.service
  fi
  set -e
}

echo "::group::Fetch canonical zCloud commit"
git clone --filter=blob:none --no-checkout https://github.com/Zennay/zCloud.git "$CANDIDATE"
git -C "$CANDIDATE" fetch --depth 1 origin "$ZSSH_CANONICAL_SHA"
git -C "$CANDIDATE" checkout --detach "$ZSSH_CANONICAL_SHA"
test "$(git -C "$CANDIDATE" rev-parse HEAD)" = "$ZSSH_CANONICAL_SHA"
echo "::endgroup::"

echo "::group::Verify zSSH security tests"
npm install --prefix "$CANDIDATE/zssh" --ignore-scripts --no-audit --no-fund
npm test --prefix "$CANDIDATE/zssh"
echo "::endgroup::"

echo "::group::Install versioned zSSH runtime"
RELEASE="$RELEASE_ROOT/releases/$ZSSH_CANONICAL_SHA"
rm -rf "$RELEASE"
mkdir -p "$RELEASE" "$CONFIG_ROOT" "$SERVICE_ROOT" /home/ubuntu/.local/state/zssh
cp -a "$CANDIDATE/zssh/." "$RELEASE/"
npm install --prefix "$RELEASE" --omit=dev --ignore-scripts --no-audit --no-fund

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  TOKEN="$(openssl rand -hex 32)"
  cat > "$ENV_FILE" <<EOF
NODE_ENV=production
ZSSH_DEV_BEARER_TOKEN=$TOKEN
ZSSH_EXEC_MODE=disabled
ZSSH_SAFE_PROGRAMS=uptime,whoami,id,uname,pwd,df,free
ZSSH_ALLOWED_ROOTS=/home/ubuntu/zennay-cloud
ZSSH_COMMAND_TIMEOUT_SECONDS=30
ZSSH_MAX_OUTPUT_BYTES=131072
ZSSH_MAX_FILE_BYTES=131072
ZSSH_AUDIT_LOG=/home/ubuntu/.local/state/zssh/audit.jsonl
PORT=8788
EOF
  chmod 600 "$ENV_FILE"
  unset TOKEN
fi

grep -q '^NODE_ENV=production$' "$ENV_FILE"
grep -q '^ZSSH_EXEC_MODE=disabled$' "$ENV_FILE"
grep -q '^ZSSH_DEV_BEARER_TOKEN=.' "$ENV_FILE"
chmod 600 "$ENV_FILE"

NODE_BIN="$(command -v node)"
test -x "$NODE_BIN"

if [[ -L "$RELEASE_ROOT/current" ]]; then
  OLD_TARGET="$(readlink -f "$RELEASE_ROOT/current" || true)"
fi
if systemctl --user is-active --quiet zssh.service 2>/dev/null; then
  OLD_ACTIVE=1
fi

ln -sfn "$RELEASE" "$RELEASE_ROOT/current"
RUNTIME_SWITCHED=1

cat > "$SERVICE_ROOT/zssh.service" <<EOF
[Unit]
Description=zSSH secure remote MCP gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/.local/share/zssh/current
EnvironmentFile=/home/ubuntu/.config/zssh/zssh.env
ExecStart=$NODE_BIN /home/ubuntu/.local/share/zssh/current/server.mjs
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable zssh.service >/dev/null
if ! systemctl --user restart zssh.service; then
  rollback_runtime
  exit 1
fi
echo "::endgroup::"

runtime_canary() {
  echo "::group::Runtime health and auth canary"
  local healthy=0
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8788/health >/tmp/zssh-health.json; then
      healthy=1
      break
    fi
    sleep 1
  done
  [[ "$healthy" = "1" ]]

  python3 -c 'import json; d=json.load(open("/tmp/zssh-health.json")); assert d.get("ok") is True and d.get("service")=="zssh"; print("ZSSH_HEALTH_GREEN")'

  local unauthorized
  unauthorized="$(curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8788/mcp -H 'content-type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"ping"}')"
  [[ "$unauthorized" = "401" ]]
  echo "ZSSH_AUTH_FAIL_CLOSED_GREEN"
  echo "::endgroup::"

  echo "::group::MCP capability canary"
  local token
  token="$(sed -n 's/^ZSSH_DEV_BEARER_TOKEN=//p' "$ENV_FILE")"
  [[ -n "$token" ]]
  export ZSSH_CANARY_TOKEN="$token"
  unset token

  cat > "$RELEASE/.zssh-canary.mjs" <<'NODE'
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(
  new URL("http://127.0.0.1:8788/mcp"),
  { requestInit: { headers: { Authorization: `Bearer ${process.env.ZSSH_CANARY_TOKEN}` } } }
);
const client = new Client({ name: "zssh-runtime-canary", version: "1.0.0" });
await client.connect(transport);

const listed = await client.listTools();
const names = new Set(listed.tools.map(t => t.name));
for (const required of ["zssh_server_info", "zssh_run_safe", "zssh_read_file", "zssh_write_file", "zssh_exec"]) {
  if (!names.has(required)) throw new Error("missing MCP tool: " + required);
}

const textJson = (response) => {
  const block = response.content?.find(item => item.type === "text");
  if (!block) throw new Error("missing text response");
  return JSON.parse(block.text);
};

const info = textJson(await client.callTool({ name: "zssh_server_info", arguments: {} }));
if (info.exec_mode !== "disabled") throw new Error("raw exec must stay disabled");

const safe = textJson(await client.callTool({
  name: "zssh_run_safe",
  arguments: { program: "whoami", args: [], cwd: "/home/ubuntu/zennay-cloud", timeout_seconds: 5 }
}));
if (!safe.ok || safe.stdout.trim() !== "ubuntu") throw new Error("safe runner canary failed");

const write = textJson(await client.callTool({
  name: "zssh_write_file",
  arguments: { path: "/home/ubuntu/zennay-cloud/.zssh-canary.txt", content: "zssh-canary\n" }
}));
if (!write.ok) throw new Error("write canary failed");

const read = textJson(await client.callTool({
  name: "zssh_read_file",
  arguments: { path: "/home/ubuntu/zennay-cloud/.zssh-canary.txt" }
}));
if (read.content !== "zssh-canary\n") throw new Error("read-after-write canary failed");

const raw = textJson(await client.callTool({
  name: "zssh_exec",
  arguments: { command: "uptime", cwd: "/home/ubuntu/zennay-cloud", timeout_seconds: 5 }
}));
if (raw.ok !== false || raw.blocked !== true) throw new Error("raw shell did not fail closed");

await client.close();
console.log("ZSSH_MCP_CANARY_GREEN");
NODE

  node "$RELEASE/.zssh-canary.mjs"
  rm -f "$RELEASE/.zssh-canary.mjs" "$CANARY_FILE"
  unset ZSSH_CANARY_TOKEN
  echo "::endgroup::"
}

if ! runtime_canary; then
  rollback_runtime
  exit 1
fi

echo "::group::Guarded zCloud project registration"
if ! (
  test -x /home/ubuntu/.local/bin/zcloud-prechange-guard
  /home/ubuntu/.local/bin/zcloud-prechange-guard --root "$LIVE_ROOT" --state "$STATE_DIR"

  python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"     --candidate "$CANDIDATE"     --root "$LIVE_ROOT"     --state "$STATE_DIR"     --path projects.json     --path project-layout.json     --actor github-zssh-runtime-promote     --dry-run

  python3 "$CANDIDATE/scripts/zcloud_transactional_promote.py"     --candidate "$CANDIDATE"     --root "$LIVE_ROOT"     --state "$STATE_DIR"     --path projects.json     --path project-layout.json     --actor github-zssh-runtime-promote     --require-incidents
); then
  rollback_runtime
  exit 1
fi
echo "::endgroup::"

echo "::group::Verify zCloud live project card"
card_green=0
for _ in $(seq 1 90); do
  if python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=5) as r:
    data=json.load(r)
projects=data.get("projects") or []
match=[p for p in projects if p.get("id")=="zssh"]
assert match
print("ZCLOUD_ZSSH_CARD_GREEN", match[0].get("progress"))
PY
  then
    card_green=1
    break
  fi
  sleep 1
done
[[ "$card_green" = "1" ]]

/home/ubuntu/.local/bin/zcloud-postdeploy-canary   --root "$LIVE_ROOT"   --db "$LIVE_ROOT/history.db"   --require-incidents

systemctl --user is-active --quiet zssh.service
echo "ZSSH_GITHUB_PROMOTION_GREEN"
echo "::endgroup::"
