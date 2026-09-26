"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  parseArgs,
  parseTeam,
  resolveRoomToken,
  resolveLiveChampion,
  isEliteBot,
  summarizeStatus,
} = require("../tools/elite_live_room");

function withTempDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "haxlab-live-room-"));
  try {
    return fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

assert.strictEqual(parseTeam("red"), 1);
assert.strictEqual(parseTeam("2"), 2);
assert.throws(() => parseTeam("spectators"), /invalid bot team/);

const parsed = parseArgs(
  ["--team", "blue", "--private", "--score-limit", "7", "--dry-run"],
  {},
);
assert.strictEqual(parsed.botTeam, 2);
assert.strictEqual(parsed.humanTeam, 1);
assert.strictEqual(parsed.showInRoomList, false);
assert.strictEqual(parsed.scoreLimit, 7);
assert.strictEqual(parsed.dryRun, true);

assert.strictEqual(
  resolveRoomToken({ HAXBALL_HEADLESS_TOKEN: " abc " }),
  "abc",
);

withTempDir((dir) => {
  const tokenPath = path.join(dir, "token.txt");
  fs.writeFileSync(tokenPath, "from-file\n");
  assert.strictEqual(
    resolveRoomToken({ HAXBALL_HEADLESS_TOKEN_FILE: tokenPath }),
    "from-file",
  );
});

withTempDir((dir) => {
  const runtimePath = path.join(dir, "runtime-model.json");
  const pointerPath = path.join(dir, "live.json");
  fs.writeFileSync(runtimePath, "{}\n");
  fs.writeFileSync(pointerPath, JSON.stringify({
    schema: "haxlab-champion-pointer-v1",
    version_id: "test-live-v1",
    runtime_model_path: runtimePath,
    validation_stage: "live",
  }));
  const resolved = resolveLiveChampion({ pointerPath, modelDir: dir });
  assert.strictEqual(resolved.version_id, "test-live-v1");
  assert.strictEqual(resolved.validation_stage, "live");
});

withTempDir((dir) => {
  const runtimePath = path.join(dir, "runtime-model.json");
  const pointerPath = path.join(dir, "canary.json");
  fs.writeFileSync(runtimePath, "{}\n");
  fs.writeFileSync(pointerPath, JSON.stringify({
    schema: "haxlab-champion-pointer-v1",
    version_id: "test-canary-v1",
    runtime_model_path: runtimePath,
    validation_stage: "canary",
  }));
  assert.throws(
    () => resolveLiveChampion({ pointerPath, modelDir: dir }),
    /requires an explicitly activated live champion/,
  );
});

assert.strictEqual(isEliteBot({ id: 65000 }), true);
assert.strictEqual(isEliteBot({ id: 7, auth: "haxlab-elite-dm" }), true);
assert.strictEqual(isEliteBot({ id: 7, auth: "human-auth" }), false);

const summary = summarizeStatus({
  getEliteRuntimeStatus() {
    return {
      policy: { version_id: "champion-v1" },
      runtime_errors: 0,
      bots: [{ id: 65000, role: "gk" }],
    };
  },
});
assert.strictEqual(summary.schema, "haxlab-live-room-status-v1");
assert.strictEqual(summary.policy.version_id, "champion-v1");
assert.strictEqual(summary.bots.length, 1);

console.log("test_elite_live_room: ok");
