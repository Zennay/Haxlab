"use strict";

const assert = require("assert");
const {
  attachFutureSignal,
  futureMotionAssist,
} = require("../tools/elite_tactics");

const champion = {
  dir_x: 0,
  dir_y: 0,
  kick: true,
  kick_probability: 0.91,
  direction_probability: 0.72,
  future_head_available: true,
  future_dir_x: -1,
  future_dir_y: 0,
  future_direction_probability: 0.51,
};

const challenger = {
  dir_x: -1,
  dir_y: -1,
  kick: false,
  future_head_available: true,
  future_direction_class: 8,
  future_dir_x: 1,
  future_dir_y: 1,
  future_direction_probability: 0.83,
};

const hybrid = attachFutureSignal(champion, challenger);
assert.strictEqual(hybrid.dir_x, champion.dir_x);
assert.strictEqual(hybrid.dir_y, champion.dir_y);
assert.strictEqual(hybrid.kick, champion.kick);
assert.strictEqual(hybrid.kick_probability, champion.kick_probability);
assert.strictEqual(
  hybrid.direction_probability,
  champion.direction_probability,
);
assert.strictEqual(hybrid.future_dir_x, 1);
assert.strictEqual(hybrid.future_dir_y, 1);
assert.strictEqual(hybrid.future_direction_probability, 0.83);
assert.strictEqual(hybrid.future_signal_source, "secondary_model");

const assisted = futureMotionAssist(hybrid, 150, {
  minimumConfidence: 0.75,
  minimumBallDistance: 80,
});
assert.strictEqual(assisted.dir_x, 1);
assert.strictEqual(assisted.dir_y, 1);
assert.strictEqual(assisted.kick, true);
assert.strictEqual(assisted.future_assist_applied, true);

const movingChampion = { ...champion, dir_x: 1, dir_y: 0 };
const movingHybrid = attachFutureSignal(movingChampion, challenger);
const untouched = futureMotionAssist(movingHybrid, 150, {
  minimumConfidence: 0.75,
  minimumBallDistance: 80,
});
assert.strictEqual(untouched.dir_x, 1);
assert.strictEqual(untouched.dir_y, 0);
assert.strictEqual(untouched.future_assist_applied, undefined);

console.log("elite hybrid future assist: ok");
