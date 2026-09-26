"use strict";

const assert = require("assert");
const {
  imputeMissingEntityGroups,
} = require("../tools/elite_policy_runtime");

const columns = [
  "op1_dx", "op1_dy", "op1_dvx", "op1_dvy", "op1_present",
  "op2_dx", "op2_dy", "op2_dvx", "op2_dvy", "op2_present",
];
const mean = [10, 20, 1, 2, 0.99, 30, 40, 3, 4, 0.98];
const result = imputeMissingEntityGroups(
  {
    op1_dx: 5, op1_dy: 6, op1_dvx: 0, op1_dvy: 0, op1_present: 1,
    op2_dx: 0, op2_dy: 0, op2_dvx: 0, op2_dvy: 0, op2_present: 0,
  },
  columns,
  mean,
);

assert.deepStrictEqual(result.imputed_entities, ["op2"]);
assert.strictEqual(result.features.op1_dx, 5);
assert.strictEqual(result.features.op2_dx, 30);
assert.strictEqual(result.features.op2_dy, 40);
assert.strictEqual(result.features.op2_present, 0.98);
console.log("test_elite_policy_runtime: ok");
