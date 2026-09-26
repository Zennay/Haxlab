"use strict";

const assert = require("assert");
const {
  collectionValues,
  statePlayers,
} = require("../tools/elite_features");

const a = { id: 1 };
const b = { id: 2 };

assert.deepStrictEqual(collectionValues([a, b]), [a, b]);
assert.deepStrictEqual(collectionValues(new Map([[1, a], [2, b]])), [a, b]);
assert.deepStrictEqual(collectionValues(new Set([a, b])), [a, b]);
assert.deepStrictEqual(collectionValues({ one: a, two: b }), [a, b]);
assert.deepStrictEqual(statePlayers({ players: new Map([[1, a], [2, b]]) }), [a, b]);
assert.deepStrictEqual(statePlayers({ playerList: { one: a, two: b } }), [a, b]);
assert.deepStrictEqual(statePlayers({ players: [] }), []);

console.log("test_elite_features: ok");
