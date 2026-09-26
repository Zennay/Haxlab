"use strict";

const assert = require("assert");
const {
  lineOrderedVectors,
} = require("../tools/elite_features");

function p(id, teamId, x, y=0) {
  return {
    id,
    team: { id: teamId },
    disc: {
      pos: { x, y },
      speed: { x: 0, y: 0 },
    },
  };
}

const origin = p(10, 1, 0, 0);

// Red attacks +X: line order is increasing world X.
const red = [
  p(1, 1, 150),
  p(2, 1, -150),
  p(3, 1, 50),
  p(4, 1, -50),
];
const redVec = lineOrderedVectors(origin, red, 1, 4);
assert.deepStrictEqual(
  [redVec[0], redVec[5], redVec[10], redVec[15]],
  [-150, -50, 50, 150],
);

// Blue attacks -X: GK is at positive world X, but still slot 1.
const blue = [
  p(5, 2, -150),
  p(6, 2, 150),
  p(7, 2, -50),
  p(8, 2, 50),
];
const blueVec = lineOrderedVectors(origin, blue, 1, 4);
assert.deepStrictEqual(
  [blueVec[0], blueVec[5], blueVec[10], blueVec[15]],
  [150, 50, -50, -150],
);

// Moving the controlled player does not reshuffle the other team by distance.
origin.disc.pos.x = 140;
const stable = lineOrderedVectors(origin, red, 1, 4);
assert.deepStrictEqual(
  [stable[0], stable[5], stable[10], stable[15]],
  [-290, -190, -90, 10],
);

console.log("elite team-line ordering: ok");
