"use strict";

const assert = require("assert");
const {
  replayPlayerDiscTeam,
  sanitizeReplayPlayerDiscs,
} = require("../tools/stadium_sanitize");

function fakePlayer(team, x) {
  return {
    pos: [x, 0],
    radius: 15,
    invMass: 0.5,
    color: "0",
    cMask: ["red", "blue", "ball", "wall"],
    cGroup: [team],
  };
}

assert.strictEqual(replayPlayerDiscTeam(fakePlayer("red", -200)), "red");
assert.strictEqual(replayPlayerDiscTeam({ radius: 15, color: "0" }), null);

const ball = { radius: 5.8, color: "FFF26D", cGroup: ["ball"] };
const posts = [1, 2, 3, 4].map((x) => ({ pos: [x, 0], radius: 5.4 }));
const stadium = {
  name: "BFF Big v4",
  discs: [
    ball,
    ...posts,
    ...[0, 1, 2, 3].map((i) => fakePlayer("red", -200 - i)),
    ...[0, 1, 2, 3].map((i) => fakePlayer("blue", 200 + i)),
  ],
};
const clean = sanitizeReplayPlayerDiscs(stadium);
assert.strictEqual(clean.detected, true);
assert.strictEqual(clean.removed, 8);
assert.strictEqual(clean.stadium.discs.length, 5);

const partial = sanitizeReplayPlayerDiscs({
  discs: [ball, fakePlayer("red", -200)],
});
assert.strictEqual(partial.detected, false);
assert.strictEqual(partial.removed, 0);

console.log("test_stadium_sanitize: ok");
