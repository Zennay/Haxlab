"use strict";

const assert = require("assert");
const {
  validateDiscState,
  transformDisc,
  applyReplayScenario,
} = require("../tools/sandbox_replay_start");

const valid = { x: 12.5, y: -7, vx: 0.75, vy: -0.25 };

assert.deepStrictEqual(validateDiscState(valid, "player"), valid);
assert.deepStrictEqual(
  transformDisc(valid, false, "player"),
  { x: 12.5, y: -7, xspeed: 0.75, yspeed: -0.25 },
);
assert.deepStrictEqual(
  transformDisc(valid, true, "player"),
  { x: -12.5, y: -7, xspeed: -0.75, yspeed: -0.25 },
);

assert.throws(
  () => validateDiscState(null, "player"),
  /player must be an object/,
);
assert.throws(
  () => validateDiscState({ ...valid, x: "12.5" }, "player"),
  /player\.x must be a finite native number/,
);
assert.throws(
  () => validateDiscState({ x: 1, y: 2, vx: 3 }, "player"),
  /player\.vy must be a finite native number/,
);
assert.throws(
  () => validateDiscState({ ...valid, vx: Number.NaN }, "player"),
  /player\.vx must be a finite native number/,
);
assert.throws(
  () => validateDiscState({ ...valid, y: Number.POSITIVE_INFINITY }, "player"),
  /player\.y must be a finite native number/,
);
assert.throws(
  () => transformDisc(valid, 1, "player"),
  /scenario mirror flag must be boolean/,
);

assert.throws(
  () => applyReplayScenario(
    {},
    [],
    { teams: { "1": {}, "2": {} }, ball: valid },
    "1",
  ),
  /eliteTeamId must be native team id 1 or 2/,
);

console.log("test_replay_scenario_state_integrity: ok");
