"use strict";

const assert = require("assert");
const { ElitePolicyRuntime } = require("../tools/elite_policy_runtime");

function matrix(rows, cols, fill = 0) {
  return Array.from({ length: rows }, () => Array(cols).fill(fill));
}

function model(active) {
  const weights = {
    w1: matrix(5, 2),
    b1: [0, 0],
    w2: matrix(2, 2),
    b2: [0, 0],
    wd: matrix(2, 9),
    bd: [0, 0, 0, 0, 9, 0, 0, 0, 0],
    wk: matrix(2, 1),
    bk: [-10],
    wf: matrix(2, 9),
    bf: [0, 0, 0, 0, 9, 0, 0, 0, 0],
    recovery_wd: matrix(2, 9),
    recovery_bd: [0, 0, 0, 0, 0, 0, 0, 0, 9],
    recovery_activation_w: matrix(2, 2),
    recovery_activation_b: active ? [-9, 9] : [9, -9],
  };
  return {
    schema: "haxlab-elite-js-runtime-v1",
    source_model_schema: "haxlab-elite-temporal-policy-v1",
    window: 1,
    base_input_columns: ["own_x"],
    feature_ordering: "team-line-order-v1",
    role_ids: { gk: 0, dm: 1, am: 2, st: 3 },
    direction_classes: [
      { class_id: 0, dir_x: -1, dir_y: -1 },
      { class_id: 1, dir_x: 0, dir_y: -1 },
      { class_id: 2, dir_x: 1, dir_y: -1 },
      { class_id: 3, dir_x: -1, dir_y: 0 },
      { class_id: 4, dir_x: 0, dir_y: 0 },
      { class_id: 5, dir_x: 1, dir_y: 0 },
      { class_id: 6, dir_x: -1, dir_y: 1 },
      { class_id: 7, dir_x: 0, dir_y: 1 },
      { class_id: 8, dir_x: 1, dir_y: 1 },
    ],
    kick_threshold: 0.5,
    kick_thresholds_by_role: {},
    kick_max_distance: 31,
    mean: [0],
    std: [1],
    weights,
    recovery_routing: {
      schema: "haxlab-learned-recovery-routing-v1",
      activation_threshold: 0.5,
    },
  };
}

const off = new ElitePolicyRuntime(model(false)).act({
  agent_id: "a",
  role: "gk",
  features: { own_x: 0, ball_dx: 100, ball_dy: 0 },
});
assert.strictEqual(off.direction_class, 4);
assert.strictEqual(off.base_direction_class, 4);
assert.strictEqual(off.recovery_direction_class, 8);
assert.strictEqual(off.recovery_active, false);

const on = new ElitePolicyRuntime(model(true)).act({
  agent_id: "b",
  role: "gk",
  features: { own_x: 0, ball_dx: 100, ball_dy: 0 },
});
assert.strictEqual(on.direction_class, 8);
assert.strictEqual(on.base_direction_class, 4);
assert.strictEqual(on.recovery_direction_class, 8);
assert.strictEqual(on.recovery_active, true);
assert.strictEqual(on.future_direction_class, 4);
assert.strictEqual(on.kick, false);

console.log("candidate-g runtime routing tests passed");
