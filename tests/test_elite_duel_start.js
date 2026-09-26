"use strict";

const assert = require("assert");
const {
  makeRng,
  sampleStartState,
} = require("../tools/elite_model_duel");

const stadium = { width: 800, height: 350 };

for (const seed of [1337, 1338, 1339, 20260926]) {
  const red = sampleStartState(seed, 1, stadium);
  const blue = sampleStartState(seed, 2, stadium);

  assert.strictEqual(blue.ball_x, -red.ball_x, "ball_x must mirror by side");
  assert.strictEqual(blue.ball_vx, -red.ball_vx, "ball_vx must mirror by side");
  assert.strictEqual(blue.ball_y, red.ball_y, "ball_y must stay paired");
  assert.strictEqual(blue.ball_vy, red.ball_vy, "ball_vy must stay paired");
}

const samples = Array.from({ length: 16 }, (_, index) =>
  sampleStartState(1337 + index, 1, stadium),
);
const unique = new Set(samples.map((row) =>
  [row.ball_x, row.ball_y, row.ball_vx, row.ball_vy]
    .map((value) => value.toFixed(9))
    .join(","),
));
assert.ok(unique.size >= 15, "adjacent seeds must produce dispersed start states");

const a = makeRng(4242);
const b = makeRng(4242);
for (let index = 0; index < 8; index += 1) {
  assert.strictEqual(a(), b(), "same seed must remain deterministic");
}

console.log("elite duel start-state mirroring and seed dispersion: ok");
