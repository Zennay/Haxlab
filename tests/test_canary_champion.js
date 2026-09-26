"use strict";

const assert = require("assert");
const { compareReplay } = require("../tools/validate_multi_replay_champion");
const { validateCanary } = require("../tools/validate_canary_champion");

function benchmark({
  movement,
  progression,
  territory,
  nearBall,
  errors = 0,
  assistRate = 0.008,
  sideGap = 0.02,
}) {
  return {
    policy_activity: {
      nonzero_movement_rate: movement,
      near_ball_rate: nearBall,
      runtime_errors: errors,
      future_assist_rate: assistRate,
    },
    progression: { elite_share: progression },
    territory: { elite_half_rate: territory },
    paired_side_gap: { territory_abs: sideGap },
  };
}

const base = benchmark({
  movement: 0.32,
  progression: 0.50,
  territory: 0.45,
  nearBall: 0.004,
});

const goodRows = Array.from({ length: 10 }, (_, index) =>
  compareReplay(
    "r" + index,
    base,
    benchmark({
      movement: 0.36 + (index % 3) * 0.005,
      progression: 0.49 + (index % 2) * 0.01,
      territory: 0.445 + (index % 2) * 0.005,
      nearBall: 0.0042,
    }),
  ),
);

const good = validateCanary(goodRows);
assert.strictEqual(good.validated, true);
assert.strictEqual(good.aggregate.replay_count, 10);
assert.strictEqual(good.checks.movement_generalizes, true);
assert.strictEqual(good.checks.progression_generalizes, true);

const progressionCollapse = JSON.parse(JSON.stringify(goodRows));
progressionCollapse[0].deltas.progression = -0.10;
assert.strictEqual(
  validateCanary(progressionCollapse).checks.no_progression_collapse,
  false,
);

const narrow = JSON.parse(JSON.stringify(goodRows));
for (let i = 0; i < 4; i += 1) {
  narrow[i].deltas.movement = -0.005;
}
assert.strictEqual(
  validateCanary(narrow).checks.movement_generalizes,
  false,
);

const tooFew = goodRows.slice(0, 8);
assert.strictEqual(validateCanary(tooFew).checks.enough_replays, false);

console.log("canary champion validation: ok");
