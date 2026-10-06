"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  parseArgs,
  scenarioIndexForRow,
  loadScenarioRows,
  contextShifted,
  choosePartnerModel,
  createTracker,
  updateTracker,
  finishTracker,
  pairArenaRows,
} = require("../tools/elite_closed_loop_arena_v2");

const requiredArgs = [
  "--challenger", "challenger.json",
  "--champion", "champion.json",
  "--stadium", "stadium.hbs",
  "--scenarios", "scenarios.json",
];

const defaults = parseArgs(requiredArgs);
assert.strictEqual(defaults.seconds, 30);
assert.strictEqual(defaults.maxScenarios, 4);
assert.strictEqual(defaults.sampleEvery, 6);
assert.strictEqual(defaults.plugRepeats, 1);
assert.strictEqual(defaults.seed, 1337);
assert.deepStrictEqual(defaults.partnerModels, ["champion.json"]);

const explicit = parseArgs([
  ...requiredArgs,
  "--seconds", "30.5",
  "--max-scenarios", "3",
  "--sample-every", "4",
  "--plug-repeats", "2",
  "--seed", "42",
]);
assert.strictEqual(explicit.seconds, 30.5);
assert.strictEqual(explicit.maxScenarios, 3);
assert.strictEqual(explicit.sampleEvery, 4);
assert.strictEqual(explicit.plugRepeats, 2);
assert.strictEqual(explicit.seed, 42);

assert.throws(
  () => parseArgs([...requiredArgs, "--seconds", "NaN"]),
  /Invalid numeric value for --seconds/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--seconds", "4.99"]),
  /Out-of-range value for --seconds/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--max-scenarios", "2.5"]),
  /Invalid integer value for --max-scenarios/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--sample-every", "0"]),
  /Out-of-range integer for --sample-every/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--seed", "0"]),
  /Out-of-range integer for --seed/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--seed", "4294967296"]),
  /Out-of-range integer for --seed/,
);
assert.throws(
  () => parseArgs([...requiredArgs, "--seconds"]),
  /Missing value for --seconds/,
);
assert.throws(
  () => parseArgs([
    "--challenger", "--champion",
    "champion.json",
    "--stadium", "stadium.hbs",
    "--scenarios", "scenarios.json",
  ]),
  /Missing value for --challenger/,
);

assert.strictEqual(scenarioIndexForRow({}, 0), 1);
assert.strictEqual(scenarioIndexForRow({ scenario_index: 7 }, 0), 7);
assert.throws(
  () => scenarioIndexForRow({ scenario_index: "7" }, 0),
  /invalid scenario_index/,
);
assert.throws(
  () => scenarioIndexForRow({ scenario_index: 1.5 }, 0),
  /invalid scenario_index/,
);
assert.throws(
  () => scenarioIndexForRow({ scenario_index: 0 }, 0),
  /invalid scenario_index/,
);
assert.throws(
  () => scenarioIndexForRow(null, 0),
  /must be an object/,
);

const scenarioTemp = fs.mkdtempSync(
  path.join(os.tmpdir(), "haxlab-arena-v2-config-"),
);
try {
  const scenarioPath = path.join(scenarioTemp, "scenarios.json");
  fs.writeFileSync(
    scenarioPath,
    JSON.stringify({ scenarios: [{ scenario_index: 2 }, { scenario_index: 3 }] }),
  );
  assert.strictEqual(loadScenarioRows(scenarioPath, 2).length, 2);

  fs.writeFileSync(
    scenarioPath,
    JSON.stringify({ scenarios: [{ scenario_index: 1 }, { scenario_index: 1 }] }),
  );
  assert.throws(
    () => loadScenarioRows(scenarioPath, 2),
    /duplicate scenario_index: 1/,
  );

  fs.writeFileSync(
    scenarioPath,
    JSON.stringify({ scenarios: [{ scenario_index: 2 }, {}] }),
  );
  assert.throws(
    () => loadScenarioRows(scenarioPath, 2),
    /duplicate scenario_index: 2/,
  );

  fs.writeFileSync(
    scenarioPath,
    JSON.stringify({ scenarios: [null] }),
  );
  assert.throws(
    () => loadScenarioRows(scenarioPath, 1),
    /must be an object/,
  );

  fs.writeFileSync(
    scenarioPath,
    JSON.stringify({ scenarios: [{ scenario_index: "1" }] }),
  );
  assert.throws(
    () => loadScenarioRows(scenarioPath, 1),
    /invalid scenario_index/,
  );

  assert.throws(
    () => loadScenarioRows(scenarioPath, 0),
    /maxScenarios must be a positive safe integer/,
  );
} finally {
  fs.rmSync(scenarioTemp, { recursive: true, force: true });
}

assert.strictEqual(
  contextShifted(
    { angle: 0, distance: 100 },
    { angle: 0.1, distance: 120 },
  ),
  false,
);
assert.strictEqual(
  contextShifted(
    { angle: 0, distance: 100 },
    { angle: 0.7, distance: 105 },
  ),
  true,
);
assert.strictEqual(
  contextShifted(
    { angle: 0, distance: 100 },
    { angle: 0.1, distance: 220 },
  ),
  true,
);

const pool = ["a.json", "b.json", "c.json"];
assert.strictEqual(
  choosePartnerModel(pool, 1337, 2, 1, 0),
  choosePartnerModel(pool, 1337, 2, 1, 0),
  "partner selection must be deterministic",
);
assert.ok(
  pool.includes(choosePartnerModel(pool, 1337, 2, 1, 0)),
);

function fakePlayer(x, y) {
  return {
    team: { id: 1 },
    disc: { pos: { x, y } },
  };
}

function fakeGame(ballX, ballY) {
  return {
    physicsState: {
      discs: [{ pos: { x: ballX, y: ballY } }],
    },
  };
}

const tracker = createTracker({
  role: "am",
  modelKind: "challenger",
  modelPath: "candidate.json",
});

for (let index = 0; index < 4; index += 1) {
  updateTracker(tracker, {
    action: { dirX: 1, dirY: 0, kick: false },
    canonical: { ood_max_abs_z: 1 },
    player: fakePlayer(0, 0),
    gameState: fakeGame(100, index === 3 ? 120 : 0),
    teamId: 1,
    stadium: { width: 800, height: 350 },
    sampleEvery: 6,
  });
}
assert.ok(
  tracker.context_misses >= 1,
  "holding the same action through a strong context shift must count as a miss",
);

updateTracker(tracker, {
  action: { dirX: 0, dirY: 1, kick: false },
  canonical: { ood_max_abs_z: 1 },
  player: fakePlayer(0, 0),
  gameState: fakeGame(-100, 0),
  teamId: 1,
  stadium: { width: 800, height: 350 },
  sampleEvery: 6,
});
assert.ok(
  tracker.context_adaptations >= 1,
  "changing action after a changed context must count as adaptation",
);

const finished = finishTracker(tracker, 6);
assert.ok(finished.max_held_action_seconds > 0);
assert.ok(finished.context_adaptation_rate >= 0);
assert.ok(finished.context_adaptation_rate <= 1);



function pairRoleMetrics() {
  return {
    near_ball_rate: 0.2,
    boundary_rate: 0.0,
    ood_rate: 0.01,
    far_stall_rate: 0.05,
    context_adaptation_rate: 0.75,
    average_ball_distance: 200,
    average_role_deviation: 120,
    max_held_action_seconds: 1.5,
    runtime_errors: 0,
  };
}

function pairTeamRoles() {
  return Object.fromEntries(
    ["gk", "dm", "am", "st"].map((role) => [role, pairRoleMetrics()]),
  );
}

function pairLineup(kind, testedRole = null) {
  return Object.fromEntries(
    ["gk", "dm", "am", "st"].map((role) => [
      role,
      {
        model_kind:
          testedRole && role !== testedRole ? "partner" : kind,
        model_path:
          testedRole && role !== testedRole
            ? "partner-" + role + ".json"
            : kind + ".json",
      },
    ]),
  );
}

const candidateIdentityRow = {
  mode: "full_team",
  tested_kind: "challenger",
  tested_role: null,
  scenario_index: 1,
  test_team_id: 1,
  repeat_index: 0,
  lineup: pairLineup("challenger"),
  proxy: { test_score: 0.123 },
  goals: { differential: 0 },
  progression: { test_share: 0.5 },
  possession_proxy: { test_rate: 0.5 },
  team_shape: {
    formation_order_rate: 0.8,
    collapsed_rate: 0.1,
    overstretched_rate: 0.05,
  },
  test_team: { roles: pairTeamRoles() },
};
const referenceIdentityRow = {
  ...candidateIdentityRow,
  tested_kind: "reference",
  lineup: pairLineup("reference"),
  test_team: { roles: pairTeamRoles() },
};
const identityPair = pairArenaRows(
  candidateIdentityRow,
  referenceIdentityRow,
);
assert.strictEqual(identityPair.result, "draw");
assert.strictEqual(identityPair.proxy_delta, 0);

assert.throws(
  () => pairArenaRows(
    { ...candidateIdentityRow, tested_kind: "reference" },
    referenceIdentityRow,
  ),
  /candidate tested_kind must be challenger/,
);
assert.throws(
  () => pairArenaRows(
    candidateIdentityRow,
    { ...referenceIdentityRow, tested_kind: "challenger" },
  ),
  /reference tested_kind must be reference/,
);
assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      proxy: { test_score: "0.123" },
    },
    referenceIdentityRow,
  ),
  /candidate proxy\.test_score must be a finite number/,
);
assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      proxy: { test_score: Number.NaN },
    },
    referenceIdentityRow,
  ),
  /candidate proxy\.test_score must be a finite number/,
);
assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      possession_proxy: { test_rate: "0.5" },
    },
    referenceIdentityRow,
  ),
  /candidate possession_proxy\.test_rate must be a finite number/,
);
assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      team_shape: {
        ...candidateIdentityRow.team_shape,
        collapsed_rate: 1.01,
      },
    },
    referenceIdentityRow,
  ),
  /candidate team_shape\.collapsed_rate is out of range/,
);
assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      test_team: {
        roles: {
          ...candidateIdentityRow.test_team.roles,
          gk: {
            ...candidateIdentityRow.test_team.roles.gk,
            runtime_errors: 0.5,
          },
        },
      },
    },
    referenceIdentityRow,
  ),
  /candidate gk\.runtime_errors must be a safe integer/,
);
assert.throws(
  () => pairArenaRows(
    { ...candidateIdentityRow, test_team_id: 3 },
    referenceIdentityRow,
  ),
  /candidate has invalid test_team_id/,
);
const candidatePlugRow = {
  ...candidateIdentityRow,
  mode: "plug_and_play",
  tested_role: "am",
  lineup: pairLineup("challenger", "am"),
};
const referencePlugRow = {
  ...referenceIdentityRow,
  mode: "plug_and_play",
  tested_role: "am",
  lineup: pairLineup("reference", "am"),
};
const plugIdentityPair = pairArenaRows(
  candidatePlugRow,
  referencePlugRow,
);
assert.strictEqual(plugIdentityPair.result, "draw");

assert.throws(
  () => pairArenaRows(
    candidatePlugRow,
    {
      ...referencePlugRow,
      lineup: {
        ...referencePlugRow.lineup,
        gk: {
          ...referencePlugRow.lineup.gk,
          model_path: "different-partner.json",
        },
      },
    },
  ),
  /arena paired partner lineup mismatch for role gk/,
);

assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      lineup: {
        ...candidateIdentityRow.lineup,
        st: {
          ...candidateIdentityRow.lineup.st,
          model_kind: "partner",
        },
      },
    },
    referenceIdentityRow,
  ),
  /candidate lineup\.st model_kind must be challenger/,
);

assert.throws(
  () => pairArenaRows(
    {
      ...candidateIdentityRow,
      mode: "plug_and_play",
      tested_role: "sweeper",
    },
    {
      ...referenceIdentityRow,
      mode: "plug_and_play",
      tested_role: "sweeper",
    },
  ),
  /candidate plug_and_play row has invalid tested_role/,
);
assert.throws(
  () => pairArenaRows(
    candidateIdentityRow,
    referenceIdentityRow,
    Number.NaN,
  ),
  /arena tie margin must be a finite rate/,
);

console.log("test_closed_loop_arena_v2: ok");
