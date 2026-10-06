"use strict";

const assert = require("assert");
const {
  ElitePolicyRuntime,
  dense,
  softmax,
} = require("../tools/elite_policy_runtime");

function zeroMatrix(rows, columns) {
  return Array.from(
    { length: rows },
    () => Array.from({ length: columns }, () => 0),
  );
}

function runtimeModel() {
  const inputColumns = ["ball_dx", "ball_dy"];
  const inputDim = inputColumns.length + 4;
  return {
    schema: "haxlab-elite-js-runtime-v1",
    window: 1,
    base_input_columns: inputColumns,
    feature_ordering: "nearest-distance-v1",
    mean: [0, 0],
    std: [1, 1],
    role_ids: { gk: 0, dm: 1, am: 2, st: 3 },
    direction_classes: [
      { dir_x: 0, dir_y: 0 },
      { dir_x: 1, dir_y: 0 },
    ],
    kick_threshold: 0.5,
    kick_thresholds_by_role: {
      gk: 0.5,
      dm: 0.5,
      am: 0.5,
      st: 0.5,
    },
    kick_max_distance: 31,
    weights: {
      w1: zeroMatrix(inputDim, 2),
      b1: [0, 0],
      w2: zeroMatrix(2, 2),
      b2: [0, 0],
      wd: zeroMatrix(2, 2),
      bd: [0, 0],
      wk: zeroMatrix(2, 1),
      bk: [0],
      wf: zeroMatrix(2, 2),
      bf: [0, 0],
    },
  };
}

function cloneModel() {
  return JSON.parse(JSON.stringify(runtimeModel()));
}

const runtime = new ElitePolicyRuntime(runtimeModel());
const action = runtime.act({
  agent_id: "unit",
  role: "gk",
  features: { ball_dx: 10, ball_dy: 0 },
});
assert.ok(Number.isFinite(action.direction_probability));
assert.ok(Number.isFinite(action.future_direction_probability));
assert.ok(Number.isFinite(action.kick_probability));
assert.strictEqual(action.history_frames, 1);

{
  const model = cloneModel();
  model.window = "1";
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /window must be a safe integer/,
  );
}

{
  const model = cloneModel();
  model.mean[0] = "0";
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /mean\[0\] must be a finite number/,
  );
}

{
  const model = cloneModel();
  model.kick_threshold = Number.NaN;
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /kick_threshold must be a finite number/,
  );
}

{
  const model = cloneModel();
  model.kick_threshold = 1.01;
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /kick_threshold out of range/,
  );
}

{
  const model = cloneModel();
  model.kick_max_distance = -1;
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /kick_max_distance out of range/,
  );
}

{
  const model = cloneModel();
  model.role_ids.gk = "0";
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /role_ids\.gk must be a safe integer/,
  );
}

{
  const model = cloneModel();
  model.direction_classes[0].dir_x = "0";
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /direction_classes\[0\]\.dir_x must be a finite number/,
  );
}

{
  const model = cloneModel();
  model.weights.w1[0][0] = "0";
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /weights\.w1\[0\]\[0\] must be a finite number/,
  );
}

{
  const model = cloneModel();
  delete model.weights.bf;
  assert.throws(
    () => new ElitePolicyRuntime(model),
    /future head requires both weights\.wf and weights\.bf/,
  );
}

{
  const policy = new ElitePolicyRuntime(runtimeModel());
  assert.throws(
    () => policy.act({
      agent_id: "string-feature",
      role: "gk",
      features: { ball_dx: "10", ball_dy: 0 },
    }),
    /model feature ball_dx must be a finite number/,
  );
}

{
  const policy = new ElitePolicyRuntime(runtimeModel());
  assert.throws(
    () => policy.act({
      agent_id: "missing-feature",
      role: "gk",
      features: { ball_dx: 10 },
    }),
    /missing model feature: ball_dy/,
  );
}

assert.throws(
  () => dense(["1"], [[0]], [0]),
  /dense input\[0\] must be a finite number/,
);
assert.throws(
  () => softmax([0, Number.NaN]),
  /softmax logits\[1\] must be a finite number/,
);

console.log("test_elite_policy_runtime: ok");
