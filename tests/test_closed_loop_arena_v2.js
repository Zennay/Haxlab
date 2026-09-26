
"use strict";

const assert = require("assert");
const {
  contextShifted,
  choosePartnerModel,
  createTracker,
  updateTracker,
  finishTracker,
  pairArenaRows,
} = require("../tools/elite_closed_loop_arena_v2");

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



const identityRow = {
  mode: "full_team",
  tested_role: null,
  scenario_index: 1,
  test_team_id: 1,
  repeat_index: 0,
  proxy: { test_score: 0.123 },
};
const identityPair = pairArenaRows(identityRow, { ...identityRow });
assert.strictEqual(identityPair.result, "draw");
assert.strictEqual(identityPair.proxy_delta, 0);

console.log("test_closed_loop_arena_v2: ok");