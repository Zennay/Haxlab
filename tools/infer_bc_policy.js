#!/usr/bin/env node
"use strict";

const fs = require("fs");

function usage() {
  console.error(
    "Usage: node tools/infer_bc_policy.js <policy.json> <state.json>",
  );
  process.exit(2);
}

function sigmoid(value) {
  const clipped = Math.max(-30, Math.min(30, Number(value)));
  return 1 / (1 + Math.exp(-clipped));
}

function dense(input, weights, bias) {
  const output = new Array(bias.length).fill(0);
  for (let j = 0; j < bias.length; j += 1) {
    let value = Number(bias[j]);
    for (let i = 0; i < input.length; i += 1) {
      value += Number(input[i]) * Number(weights[i][j]);
    }
    output[j] = value;
  }
  return output;
}

function argmax(values) {
  let bestIndex = 0;
  let bestValue = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    if (values[i] > bestValue) {
      bestIndex = i;
      bestValue = values[i];
    }
  }
  return bestIndex;
}

function stateVector(policy, state) {
  if (Array.isArray(state)) {
    if (state.length !== policy.input_columns.length) {
      throw new Error(
        `state array width ${state.length} != expected ${policy.input_columns.length}`,
      );
    }
    return state.map(Number);
  }

  return policy.input_columns.map((column) => {
    if (!(column in state)) {
      throw new Error(`missing state feature: ${column}`);
    }
    return Number(state[column]);
  });
}

function predict(policy, state) {
  if (policy.schema !== "haxlab-bc-policy-v1") {
    throw new Error(`unsupported policy schema: ${policy.schema}`);
  }

  const raw = stateVector(policy, state);
  const mean = policy.normalization.mean;
  const std = policy.normalization.std;
  if (
    raw.length !== mean.length ||
    raw.length !== std.length
  ) {
    throw new Error("normalization width mismatch");
  }

  const normalized = raw.map((value, i) => {
    const denominator = Number(std[i]) || 1;
    return (Number(value) - Number(mean[i])) / denominator;
  });

  const w = policy.weights;
  const pre = dense(normalized, w.w1, w.b1);
  const hidden = pre.map((value) => Math.max(0, value));
  const directionLogits = dense(hidden, w.wd, w.bd);

  let kickLogit = Number(w.bk[0]);
  for (let i = 0; i < hidden.length; i += 1) {
    kickLogit += hidden[i] * Number(w.wk[i][0]);
  }

  const classId = argmax(directionLogits);
  const direction = policy.direction_classes[classId];
  const kickProbability = sigmoid(kickLogit);
  const threshold = Number(policy.kick_threshold);

  return {
    dir_x: Number(direction.dir_x),
    dir_y: Number(direction.dir_y),
    kick: kickProbability >= threshold,
    kick_probability: kickProbability,
    kick_threshold: threshold,
    direction_class: classId,
    direction_logits: directionLogits,
  };
}

if (require.main === module) {
  const policyPath = process.argv[2];
  const statePath = process.argv[3];
  if (!policyPath || !statePath) usage();

  const policy = JSON.parse(fs.readFileSync(policyPath, "utf8"));
  const state = JSON.parse(fs.readFileSync(statePath, "utf8"));
  process.stdout.write(JSON.stringify(predict(policy, state)));
}

module.exports = {
  predict,
};
