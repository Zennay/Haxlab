"use strict";

const assert = require("assert");
const {
  RecoveryTraceRecorder,
  failureLabels,
} = require("../tools/recovery_trace");

assert.deepStrictEqual(
  failureLabels(
    { boundary: true, role_deviation: 500, far_stall: false, context_miss: true, ood_max_abs_z: 9 },
    { collapsed: true, overstretched: false },
    "full_team",
  ).sort(),
  ["boundary", "context_miss", "ood", "role_deviation", "team_shape_collapse"].sort(),
);

const recorder = new RecoveryTraceRecorder({
  enabled: true,
  mode: "full_team",
  scenarioIndex: 2,
  testTeamId: 1,
  seed: 1337,
  maxPerFailure: 1,
  minGapTicks: 0,
  historySamples: 2,
});

function observe(tick, boundary) {
  recorder.observe({
    bot: { id: 1, role: "gk", modelKind: "challenger" },
    features: { player_x: tick },
    action: { dirX: 1, dirY: 0, kick: false },
    canonical: { dir_x: 1, dir_y: 0, ood_max_abs_z: 0 },
    diagnostics: {
      context: { distance: 300, angle: 0 },
      boundary,
      role_deviation: null,
      far_stall: false,
      context_miss: false,
      ood_max_abs_z: 0,
    },
    playerPosition: { x: tick, y: 0 },
    ballPosition: { x: 0, y: 0 },
  });
  recorder.finishTick({ tick, formation: { collapsed: false, overstretched: false } });
}

observe(0, false);
observe(6, true);
observe(12, true);
assert.strictEqual(recorder.rows.length, 1);
assert.strictEqual(recorder.rows[0].failure_types[0], "boundary");
assert.strictEqual(recorder.rows[0].recovery_window.length, 2);
assert.strictEqual(recorder.rows[0].recovery_window[0].tick, 0);
assert.strictEqual(recorder.rows[0].recovery_window[1].tick, 6);

console.log("recovery trace tests passed");