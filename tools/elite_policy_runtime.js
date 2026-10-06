"use strict";

const fs = require("fs");
const readline = require("readline");

const ROLE_IDS = Object.freeze({ gk: 0, dm: 1, am: 2, st: 3 });

function requireFiniteNumber(
  value,
  label,
  { minimum = -Infinity, maximum = Infinity } = {},
) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  if (value < minimum || value > maximum) {
    throw new Error(
      `${label} out of range: ${value} not in [${minimum}, ${maximum}]`,
    );
  }
  return value;
}

function requireInteger(
  value,
  label,
  { minimum = Number.MIN_SAFE_INTEGER, maximum = Number.MAX_SAFE_INTEGER } = {},
) {
  if (!Number.isSafeInteger(value)) {
    throw new Error(`${label} must be a safe integer`);
  }
  if (value < minimum || value > maximum) {
    throw new Error(
      `${label} out of range: ${value} not in [${minimum}, ${maximum}]`,
    );
  }
  return value;
}

function requireStringArray(value, label) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`${label} must be a non-empty array`);
  }
  const result = value.map((item, index) => {
    if (typeof item !== "string" || item.trim() === "") {
      throw new Error(`${label}[${index}] must be a non-empty string`);
    }
    return item;
  });
  if (new Set(result).size !== result.length) {
    throw new Error(`${label} contains duplicate values`);
  }
  return result;
}

function requireFiniteVector(value, label) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  return value.map((item, index) =>
    requireFiniteNumber(item, `${label}[${index}]`),
  );
}

function validateNumericArrayTree(value, label) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  for (let index = 0; index < value.length; index += 1) {
    const item = value[index];
    if (Array.isArray(item)) {
      validateNumericArrayTree(item, `${label}[${index}]`);
    } else {
      requireFiniteNumber(item, `${label}[${index}]`);
    }
  }
  return value;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function sigmoid(value) {
  const clipped = clamp(
    requireFiniteNumber(value, "sigmoid input"),
    -30,
    30,
  );
  return 1 / (1 + Math.exp(-clipped));
}

function softmax(logits) {
  if (!Array.isArray(logits) || logits.length === 0) {
    throw new Error("softmax logits must be a non-empty array");
  }
  const finiteLogits = logits.map((value, index) =>
    requireFiniteNumber(value, `softmax logits[${index}]`),
  );
  let maxValue = -Infinity;
  for (const value of finiteLogits) {
    if (value > maxValue) maxValue = value;
  }
  const exp = new Array(finiteLogits.length);
  let total = 0;
  for (let i = 0; i < finiteLogits.length; i += 1) {
    const value = Math.exp(finiteLogits[i] - maxValue);
    exp[i] = value;
    total += value;
  }
  requireFiniteNumber(total, "softmax total", { minimum: Number.MIN_VALUE });
  return exp.map((value) => value / total);
}

function dense(input, weights, bias, relu = false) {
  if (!Array.isArray(input)) {
    throw new Error("dense input must be an array");
  }
  if (!Array.isArray(weights) || weights.length !== input.length) {
    throw new Error(
      `dense shape mismatch: input=${input.length}, weights=${weights?.length}`,
    );
  }
  if (!Array.isArray(bias) || bias.length === 0) {
    throw new Error("dense bias must be a non-empty array");
  }
  const outputDim = bias.length;
  const output = bias.map((value, index) =>
    requireFiniteNumber(value, `dense bias[${index}]`),
  );
  for (let i = 0; i < input.length; i += 1) {
    const row = weights[i];
    if (!Array.isArray(row) || row.length !== outputDim) {
      throw new Error(
        `dense weight row ${i} has ${row?.length} values; expected ${outputDim}`,
      );
    }
    const value = requireFiniteNumber(input[i], `dense input[${i}]`);
    if (value === 0) continue;
    for (let j = 0; j < outputDim; j += 1) {
      const weight = requireFiniteNumber(
        row[j],
        `dense weights[${i}][${j}]`,
      );
      output[j] += value * weight;
      requireFiniteNumber(output[j], `dense output[${j}]`);
    }
  }
  if (relu) {
    for (let j = 0; j < output.length; j += 1) {
      if (output[j] < 0) output[j] = 0;
    }
  }
  return output;
}

class ElitePolicyRuntime {
  constructor(model) {
    if (!model || model.schema !== "haxlab-elite-js-runtime-v1") {
      throw new Error(
        `unsupported runtime model schema: ${model?.schema || "missing"}`,
      );
    }
    if (typeof model !== "object" || Array.isArray(model)) {
      throw new Error("runtime model must be an object");
    }

    this.model = model;
    this.window = requireInteger(
      model.window,
      "window",
      { minimum: 1 },
    );
    this.inputColumns = requireStringArray(
      model.base_input_columns,
      "base_input_columns",
    );
    this.featureOrdering =
      model.feature_ordering == null
        ? "nearest-distance-v1"
        : (() => {
            if (
              typeof model.feature_ordering !== "string" ||
              model.feature_ordering.trim() === ""
            ) {
              throw new Error("feature_ordering must be a non-empty string");
            }
            return model.feature_ordering;
          })();

    this.mean = requireFiniteVector(model.mean, "mean");
    this.std = requireFiniteVector(model.std, "std");
    if (this.mean.length !== this.inputColumns.length) {
      throw new Error("mean/input column length mismatch");
    }
    if (this.std.length !== this.inputColumns.length) {
      throw new Error("std/input column length mismatch");
    }

    const configuredRoleIds = model.role_ids ?? {};
    if (
      typeof configuredRoleIds !== "object" ||
      configuredRoleIds === null ||
      Array.isArray(configuredRoleIds)
    ) {
      throw new Error("role_ids must be an object");
    }
    this.roleIds = { ...ROLE_IDS, ...configuredRoleIds };
    for (const [role, value] of Object.entries(this.roleIds)) {
      requireInteger(
        value,
        `role_ids.${role}`,
        { minimum: 0, maximum: 3 },
      );
    }

    if (
      !Array.isArray(model.direction_classes) ||
      model.direction_classes.length === 0
    ) {
      throw new Error("direction_classes must be a non-empty array");
    }
    this.directionClasses = model.direction_classes.map((row, index) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) {
        throw new Error(`direction_classes[${index}] must be an object`);
      }
      return {
        ...row,
        dir_x: requireFiniteNumber(
          row.dir_x,
          `direction_classes[${index}].dir_x`,
        ),
        dir_y: requireFiniteNumber(
          row.dir_y,
          `direction_classes[${index}].dir_y`,
        ),
      };
    });

    this.kickThreshold = requireFiniteNumber(
      model.kick_threshold,
      "kick_threshold",
      { minimum: 0, maximum: 1 },
    );

    const configuredKickThresholds = model.kick_thresholds_by_role ?? {};
    if (
      typeof configuredKickThresholds !== "object" ||
      configuredKickThresholds === null ||
      Array.isArray(configuredKickThresholds)
    ) {
      throw new Error("kick_thresholds_by_role must be an object");
    }
    this.kickThresholdsByRole = Object.fromEntries(
      Object.entries(configuredKickThresholds).map(([role, value]) => [
        role,
        requireFiniteNumber(
          value,
          `kick_thresholds_by_role.${role}`,
          { minimum: 0, maximum: 1 },
        ),
      ]),
    );
    this.kickMaxDistance = requireFiniteNumber(
      model.kick_max_distance ?? 31.0,
      "kick_max_distance",
      { minimum: 0 },
    );

    if (
      !model.weights ||
      typeof model.weights !== "object" ||
      Array.isArray(model.weights)
    ) {
      throw new Error("weights must be an object");
    }
    this.weights = model.weights;
    for (const key of ["w1", "b1", "w2", "b2", "wd", "bd", "wk", "bk"]) {
      validateNumericArrayTree(this.weights[key], `weights.${key}`);
    }
    const hasFutureWeights = this.weights.wf != null;
    const hasFutureBias = this.weights.bf != null;
    if (hasFutureWeights !== hasFutureBias) {
      throw new Error("future head requires both weights.wf and weights.bf");
    }
    if (hasFutureWeights) {
      validateNumericArrayTree(this.weights.wf, "weights.wf");
      validateNumericArrayTree(this.weights.bf, "weights.bf");
    }
    this.futureHeadAvailable = hasFutureWeights && hasFutureBias;
    this.history = new Map();
  }

  static fromFile(path) {
    return new ElitePolicyRuntime(
      JSON.parse(fs.readFileSync(path, "utf8")),
    );
  }

  reset(agentId = null) {
    if (agentId == null) {
      this.history.clear();
    } else {
      this.history.delete(String(agentId));
    }
  }

  roleId(role) {
    if (Number.isInteger(role)) {
      if (role >= 0 && role <= 3) return role;
      throw new Error(`invalid role id: ${role}`);
    }
    const key = String(role).trim().toLowerCase();
    if (!Object.prototype.hasOwnProperty.call(this.roleIds, key)) {
      throw new Error(`unknown role: ${role}`);
    }
    const value = this.roleIds[key];
    requireInteger(
      value,
      `role_ids.${key}`,
      { minimum: 0, maximum: 3 },
    );
    return value;
  }

  vectorize(features) {
    return this.inputColumns.map((name) => {
      if (!Object.prototype.hasOwnProperty.call(features, name)) {
        throw new Error(`missing model feature: ${name}`);
      }
      return requireFiniteNumber(
        features[name],
        `model feature ${name}`,
      );
    });
  }

  normalizeFrame(frame) {
    return frame.map((value, index) => {
      const std = this.std[index];
      return (value - this.mean[index]) / (Math.abs(std) < 1e-12 ? 1 : std);
    });
  }

  act({ agent_id = "default", role, features }) {
    const agentId = String(agent_id);
    const roleId = this.roleId(role);
    const raw = this.vectorize(features);
    let frames = this.history.get(agentId);
    if (!frames) {
      frames = [];
      this.history.set(agentId, frames);
    }
    frames.push(raw);
    if (frames.length > this.window) {
      frames.splice(0, frames.length - this.window);
    }

    const currentNormalized = this.normalizeFrame(raw);
    const oodMeanAbs =
      currentNormalized.reduce((sum, value) => sum + Math.abs(value), 0) /
      Math.max(1, currentNormalized.length);
    const oodMaxAbs = currentNormalized.reduce(
      (best, value) => Math.max(best, Math.abs(value)),
      0,
    );

    const sequence = [];
    const first = frames[0];
    for (let i = frames.length; i < this.window; i += 1) {
      sequence.push(first);
    }
    sequence.push(...frames);

    const input = [];
    for (const frame of sequence.slice(-this.window)) {
      input.push(...this.normalizeFrame(frame));
    }
    for (let roleIndex = 0; roleIndex < 4; roleIndex += 1) {
      input.push(roleIndex === roleId ? 1 : 0);
    }

    const h1 = dense(
      input,
      this.weights.w1,
      this.weights.b1,
      true,
    );
    const h2 = dense(
      h1,
      this.weights.w2,
      this.weights.b2,
      true,
    );
    const directionLogits = dense(
      h2,
      this.weights.wd,
      this.weights.bd,
      false,
    );
    const directionProbabilities = softmax(directionLogits);
    const futureLogits = this.futureHeadAvailable
      ? dense(h2, this.weights.wf, this.weights.bf, false)
      : directionLogits.slice();
    const futureProbabilities = softmax(futureLogits);
    let directionClass = 0;
    for (let i = 1; i < directionProbabilities.length; i += 1) {
      if (
        directionProbabilities[i] >
        directionProbabilities[directionClass]
      ) {
        directionClass = i;
      }
    }

    let futureDirectionClass = 0;
    for (let i = 1; i < futureProbabilities.length; i += 1) {
      if (
        futureProbabilities[i] >
        futureProbabilities[futureDirectionClass]
      ) {
        futureDirectionClass = i;
      }
    }

    const kickLogit = dense(
      h2,
      this.weights.wk,
      this.weights.bk,
      false,
    )[0];
    const kickProbability = sigmoid(kickLogit);
    const roleKey = Object.entries(this.roleIds).find(
      ([, value]) => value === roleId,
    )?.[0];
    const roleKickThreshold =
      this.kickThresholdsByRole[roleKey] ?? this.kickThreshold;
    if (!Object.prototype.hasOwnProperty.call(features, "ball_dx")) {
      throw new Error("missing model feature: ball_dx");
    }
    if (!Object.prototype.hasOwnProperty.call(features, "ball_dy")) {
      throw new Error("missing model feature: ball_dy");
    }
    const ballDx = requireFiniteNumber(
      features.ball_dx,
      "model feature ball_dx",
    );
    const ballDy = requireFiniteNumber(
      features.ball_dy,
      "model feature ball_dy",
    );
    const ballDistance = Math.hypot(ballDx, ballDy);
    const kickInRange = ballDistance <= this.kickMaxDistance;
    const kickRequested = kickProbability >= roleKickThreshold;
    const direction = this.directionClasses[directionClass];
    const futureDirection = this.directionClasses[futureDirectionClass];
    if (!direction || !futureDirection) {
      throw new Error(`direction class ${directionClass} missing from model`);
    }

    return {
      agent_id: agentId,
      role: String(role).toLowerCase(),
      role_id: roleId,
      dir_x: direction.dir_x,
      dir_y: direction.dir_y,
      kick: kickRequested && kickInRange,
      kick_probability: kickProbability,
      kick_threshold: roleKickThreshold,
      global_kick_threshold: this.kickThreshold,
      kick_requested: kickRequested,
      kick_in_range: kickInRange,
      kick_max_distance: this.kickMaxDistance,
      ball_distance: ballDistance,
      direction_class: directionClass,
      direction_probability: directionProbabilities[directionClass],
      future_head_available: this.futureHeadAvailable,
      future_direction_class: futureDirectionClass,
      future_dir_x: futureDirection.dir_x,
      future_dir_y: futureDirection.dir_y,
      future_direction_probability:
        futureProbabilities[futureDirectionClass],
      history_frames: frames.length,
      window: this.window,
      ood_mean_abs_z: oodMeanAbs,
      ood_max_abs_z: oodMaxAbs,
    };
  }

  info() {
    return {
      schema: this.model.schema,
      source_model_schema: this.model.source_model_schema,
      window: this.window,
      feature_ordering: this.featureOrdering,
      kick_threshold: this.kickThreshold,
      kick_thresholds_by_role: { ...this.kickThresholdsByRole },
      kick_max_distance: this.kickMaxDistance,
      input_columns: this.inputColumns.slice(),
    };
  }
}

function serveStdio(modelPath) {
  const policy = ElitePolicyRuntime.fromFile(modelPath);
  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  rl.on("line", (line) => {
    if (!line.trim()) return;
    let response;
    try {
      const request = JSON.parse(line);
      const command = String(request.command || "act");
      if (command === "reset") {
        policy.reset(
          request.agent_id == null ? null : String(request.agent_id),
        );
        response = {
          ok: true,
          request_id: request.request_id ?? null,
          command: "reset",
          agent_id: request.agent_id ?? null,
        };
      } else if (command === "info") {
        response = {
          ok: true,
          request_id: request.request_id ?? null,
          ...policy.info(),
        };
      } else {
        response = {
          ok: true,
          request_id: request.request_id ?? null,
          ...policy.act(request),
        };
      }
    } catch (error) {
      response = {
        ok: false,
        error: error?.name || "Error",
        message: error?.message || String(error),
      };
    }
    process.stdout.write(JSON.stringify(response) + "\n");
  });
}

if (require.main === module) {
  const modelPath = process.argv[2];
  if (!modelPath) {
    console.error(
      "Usage: node tools/elite_policy_runtime.js /path/to/runtime-model.json",
    );
    process.exit(2);
  }
  serveStdio(modelPath);
}

module.exports = {
  ElitePolicyRuntime,
  ROLE_IDS,
  dense,
  sigmoid,
  softmax,
};