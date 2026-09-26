"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  ARENA_SCHEMA,
  actionKey,
  updateHeld,
  summarizeEpisodes,
  guardDependency,
  validateFrozenSuite,
  writeImmutable,
} = require("../tools/elite_closed_loop_arena_v2");

function roleTelemetry(overrides = {}) {
  return {
    actions: 10,
    kicks: 1,
    average_ball_distance: 100,
    min_ball_distance: 20,
    near_ball_rate: 0.1,
    raw_action_change_rate: 0.2,
    raw_max_held_decisions: 4,
    executed_action_change_rate: 0.2,
    executed_max_held_decisions: 4,
    max_stall_decisions: 3,
    long_stall_rate: 0,
    ood_mean_abs_z: 1.2,
    ood_max_abs_z: 4,
    ood_spike_rate: 0,
    average_role_target_distance: 80,
    max_role_target_distance: 150,
    role_deviation_rate: 0,
    boundary_sample_rate: 0,
    boundary_events: 0,
    guard_overrides: 0,
    guard_intervention_rate: 0,
    guard_reasons: {
      boundary: 0,
      role_leash: 0,
      stall: 0,
      ood: 0,
    },
    ...overrides,
  };
}

function episode(result = "draw", guardOverrides = 0) {
  const roles = {
    gk: roleTelemetry(),
    dm: roleTelemetry(),
    am: roleTelemetry(),
    st: roleTelemetry(),
  };
  roles.am.guard_overrides = guardOverrides;
  roles.am.guard_intervention_rate = guardOverrides / 10;
  return {
    scenario_index: 1,
    elite_team_id: 1,
    result,
    goals: {
      elite: result === "win" ? 1 : 0,
      baseline: result === "loss" ? 1 : 0,
      differential: result === "win" ? 1 : result === "loss" ? -1 : 0,
    },
    territory: {
      elite_half_rate: 0.55,
      baseline_half_rate: 0.45,
      elite_attack_third_rate: 0.3,
      baseline_attack_third_rate: 0.2,
    },
    progression: { elite_share: 0.52 },
    team_shape: {
      samples: 10,
      formation_order_rate: 0.9,
      mean_x_span: 400,
      mean_pairwise_distance: 250,
    },
    telemetry: {
      runtime_errors: 0,
      total_actions: 40,
      guard_overrides: guardOverrides,
      guard_intervention_rate: guardOverrides / 40,
      roles,
    },
  };
}

(function testActionKeyAndHeldTracking() {
  assert.strictEqual(
    actionKey({ dirX: 1, dirY: -1, kick: true }, 1),
    "1,-1,1",
  );
  assert.strictEqual(
    actionKey({ dirX: -1, dirY: -1, kick: true }, 2),
    "1,-1,1",
  );
  const bot = {
    rawLastActionKey: null,
    rawHeldStreak: 0,
    rawMaxHeldStreak: 0,
    rawActionChanges: 0,
  };
  updateHeld(bot, "1,0,0", "raw");
  updateHeld(bot, "1,0,0", "raw");
  updateHeld(bot, "0,0,0", "raw");
  assert.strictEqual(bot.rawMaxHeldStreak, 2);
  assert.strictEqual(bot.rawActionChanges, 1);
})();

(function testSummariesAndGuardDependency() {
  const raw = summarizeEpisodes([
    episode("win", 0),
    episode("draw", 0),
  ]);
  const guarded = summarizeEpisodes([
    episode("win", 1),
    episode("win", 1),
  ]);
  assert.strictEqual(raw.episodes, 2);
  assert.strictEqual(raw.match_score, 0.75);
  assert.strictEqual(guarded.match_score, 1);
  assert.strictEqual(guarded.guard_overrides, 2);
  const dep = guardDependency(raw, guarded);
  assert.strictEqual(dep.match_score_delta, 0.25);
  assert.strictEqual(dep.guard_intervention_rate, 2 / 80);
})();

(function testFrozenSuiteValidationAndImmutableWrite() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "haxlab-arena-v2-"));
  const sourceRows = [];
  for (let i = 1; i <= 3; i += 1) {
    const id = "source-0" + i;
    const dir = path.join(root, id);
    fs.mkdirSync(dir, { recursive: true });
    const replaySha = String(i).repeat(64);
    const source = {
      schema: "haxlab-replay-scenario-source-v1",
      sha256: replaySha,
    };
    const stadium = { name: "BFF Big v4", width: 800, height: 350 };
    const scenarios = {
      schema: "haxlab-replay-seeded-scenarios-v1",
      scenarios: [{ frame: i, teams: {}, history: [] }],
    };
    fs.writeFileSync(
      path.join(dir, "source.json"),
      JSON.stringify(source) + "\n",
    );
    fs.writeFileSync(
      path.join(dir, "stadium.hbs"),
      JSON.stringify(stadium) + "\n",
    );
    fs.writeFileSync(
      path.join(dir, "scenarios.json"),
      JSON.stringify(scenarios) + "\n",
    );
    const hash = (name) =>
      require("crypto")
        .createHash("sha256")
        .update(fs.readFileSync(path.join(dir, name)))
        .digest("hex");
    sourceRows.push({
      id,
      replay_sha256: replaySha,
      files_sha256: {
        "source.json": hash("source.json"),
        "stadium.hbs": hash("stadium.hbs"),
        "scenarios.json": hash("scenarios.json"),
      },
    });
  }
  fs.writeFileSync(
    path.join(root, "frozen-manifest.json"),
    JSON.stringify({
      schema: "haxlab-promotion-v2-frozen-suite-v1",
      frozen_before_next_challenger: true,
      source_count: 3,
      sources: sourceRows,
    }) + "\n",
  );
  const validated = validateFrozenSuite(root);
  assert.strictEqual(validated.sources.length, 3);
  assert.match(validated.manifest_sha256, /^[0-9a-f]{64}$/);

  const output = path.join(root, "immutable.json");
  writeImmutable(output, { schema: ARENA_SCHEMA, value: 1 });
  writeImmutable(output, { schema: ARENA_SCHEMA, value: 1 });
  assert.throws(
    () => writeImmutable(output, { schema: ARENA_SCHEMA, value: 2 }),
    /immutable arena artifact conflict/,
  );
})();

console.log("closed-loop arena v2 helper tests: ok");
