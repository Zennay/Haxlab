"use strict";

const assert = require("assert");
const { ElitePolicyRuntime } = require("../tools/elite_policy_runtime");

function matrix(rows, columns, value = 0) {
  return Array.from({ length: rows }, () => Array(columns).fill(value));
}

function validModel() {
  return {
    schema: "haxlab-elite-js-runtime-v1",
    window: 1,
    base_input_columns: ["ball_dx", "ball_dy"],
    feature_ordering: "nearest-distance-v1",
    mean: [0, 0],
    std: [1, 1],
    role_ids: { gk: 0, dm: 1, am: 2, st: 3 },
    direction_classes: [{ dir_x: 0, dir_y: 0 }],
    kick_threshold: 0.5,
    kick_thresholds_by_role: {
      gk: 0.5,
      dm: 0.5,
      am: 0.5,
      st: 0.5,
    },
    kick_max_distance: 31,
    weights: {
      w1: matrix(6, 1),
      b1: [0],
      w2: matrix(1, 1),
      b2: [0],
      wd: matrix(1, 1),
      bd: [0],
      wk: matrix(1, 1),
      bk: [0],
    },
  };
}

function expectReject(mutator, pattern) {
  const model = validModel();
  mutator(model);
  assert.throws(() => new ElitePolicyRuntime(model), pattern);
}

const runtime = new ElitePolicyRuntime(validModel());
const action = runtime.act({
  agent_id: "regression",
  role: "am",
  features: { ball_dx: 10, ball_dy: 5 },
});
assert.ok(Number.isFinite(action.kick_probability));
assert.ok(Number.isFinite(action.direction_probability));
assert.strictEqual(action.dir_x, 0);
assert.strictEqual(action.dir_y, 0);

assert.throws(
  () => runtime.act({
    agent_id: "coerced-feature",
    role: "am",
    features: { ball_dx: "10", ball_dy: 5 },
  }),
  /model feature ball_dx must be a finite native number/,
);

const overflowModel = validModel();
overflowModel.weights.w1[0][0] = 1e308;
const overflowRuntime = new ElitePolicyRuntime(overflowModel);
assert.throws(
  () => overflowRuntime.act({
    agent_id: "overflow",
    role: "am",
    features: { ball_dx: 1e308, ball_dy: 0 },
  }),
  /dense output 0 is non-finite|normalized model feature ball_dx is non-finite/,
);

expectReject((model) => {
  model.window = "1";
}, /window must be a native integer/);

expectReject((model) => {
  model.base_input_columns = ["ball_dx", "ball_dx"];
}, /must not contain duplicates/);

expectReject((model) => {
  model.mean[0] = "0";
}, /mean\[0\] must be a finite native number/);

expectReject((model) => {
  model.std[1] = Number.NaN;
}, /std\[1\] must be a finite native number/);

expectReject((model) => {
  model.kick_threshold = "0.5";
}, /kick_threshold must be a finite native number/);

expectReject((model) => {
  model.kick_thresholds_by_role.am = 1.1;
}, /kick_thresholds_by_role\.am must be within/);

expectReject((model) => {
  model.kick_max_distance = -1;
}, /kick_max_distance must be within/);

expectReject((model) => {
  model.direction_classes[0].dir_x = 2;
}, /direction_classes\[0\]\.dir_x must be within/);

expectReject((model) => {
  model.role_ids.st = true;
}, /role_ids\.st must be a native integer/);

expectReject((model) => {
  model.weights.w1[0][0] = Number.POSITIVE_INFINITY;
}, /weights\.w1\[0\]\[0\] must be a finite native number/);

expectReject((model) => {
  model.weights.b2.push(0);
}, /weights\.w2\[0\] length 1; expected 2/);

expectReject((model) => {
  model.weights.wf = matrix(1, 1);
}, /future head requires both weights\.wf and weights\.bf/);

const futureModel = validModel();
futureModel.weights.wf = matrix(1, 1);
futureModel.weights.bf = [0];
const futureRuntime = new ElitePolicyRuntime(futureModel);
assert.strictEqual(futureRuntime.futureHeadAvailable, true);

console.log("test_elite_policy_runtime_integrity: ok");
