"use strict";

const assert = require("assert");
const { futureMotionAssist } = require("../tools/elite_tactics");

const base = {
  role: "am",
  dir_x: 0,
  dir_y: 0,
  kick: false,
  future_head_available: true,
  future_dir_x: 1,
  future_dir_y: -1,
  future_direction_probability: 0.72,
};

const am = futureMotionAssist(base, 140, {
  minimumConfidence: 0.68,
  minimumBallDistance: 80,
  allowedRoles: ["am"],
});
assert.strictEqual(am.future_assist_applied, true);
assert.strictEqual(am.dir_x, 1);
assert.strictEqual(am.dir_y, -1);

const dmBlocked = futureMotionAssist(
  { ...base, role: "dm" },
  140,
  {
    minimumConfidence: 0.68,
    minimumBallDistance: 80,
    allowedRoles: ["am"],
  },
);
assert.strictEqual(dmBlocked.future_assist_applied, undefined);
assert.strictEqual(dmBlocked.dir_x, 0);
assert.strictEqual(dmBlocked.dir_y, 0);

const lowConfidence = futureMotionAssist(base, 140, {
  minimumConfidence: 0.75,
  minimumBallDistance: 80,
  allowedRoles: "am",
});
assert.strictEqual(lowConfidence.future_assist_applied, undefined);

console.log("frozen future role gate: ok");
