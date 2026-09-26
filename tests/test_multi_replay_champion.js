"use strict";

const assert = require("assert");
const {
  compareReplay,
  validate,
} = require("../tools/validate_multi_replay_champion");

function benchmark({
  movement,
  progression,
  territory,
  nearBall,
  errors = 0,
  assistRate = 0.02,
  sideGap = 0.01,
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
  movement: 0.34,
  progression: 0.58,
  territory: 0.49,
  nearBall: 0.004,
});

const goodRows = [
  compareReplay("a", base, benchmark({
    movement: 0.39, progression: 0.57, territory: 0.50, nearBall: 0.0042,
  })),
  compareReplay("b", base, benchmark({
    movement: 0.38, progression: 0.58, territory: 0.48, nearBall: 0.0041,
  })),
  compareReplay("c", base, benchmark({
    movement: 0.37, progression: 0.56, territory: 0.49, nearBall: 0.0040,
  })),
  compareReplay("d", base, benchmark({
    movement: 0.36, progression: 0.58, territory: 0.50, nearBall: 0.0042,
  })),
  compareReplay("e", base, benchmark({
    movement: 0.39, progression: 0.57, territory: 0.49, nearBall: 0.0041,
  })),
];

const good = validate(goodRows);
assert.strictEqual(good.validated, true);
assert.strictEqual(good.aggregate.replay_count, 5);
assert.strictEqual(good.aggregate.movement_nonnegative_replays, 5);
assert.strictEqual(good.checks.no_progression_collapse, true);

const collapseRows = goodRows.map((row) => JSON.parse(JSON.stringify(row)));
collapseRows[2].deltas.progression = -0.12;
const collapsed = validate(collapseRows);
assert.strictEqual(collapsed.validated, false);
assert.strictEqual(collapsed.checks.no_progression_collapse, false);

const narrowRows = goodRows.map((row) => JSON.parse(JSON.stringify(row)));
for (const row of narrowRows) row.deltas.movement = 0.005;
const narrow = validate(narrowRows);
assert.strictEqual(narrow.validated, false);
assert.strictEqual(narrow.checks.mean_movement_improves, false);

console.log("multi replay champion validation: ok");
