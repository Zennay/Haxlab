"use strict";

const fs = require("fs");
const readline = require("readline");

const ROLE_IDS = Object.freeze({ gk: 0, dm: 1, am: 2, st: 3 });

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
  requireFiniteNumber(total, "softmax total", { min: Number.MIN_VALUE });
  return exp.map((value) => value / total);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireFiniteNumber(value, label, { min = -Infinity, max = Infinity } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite native number`);
  }
  if (value < min || value > max) {
    throw new Error(`${label} must be within [${min}, ${max}]`);
  }
  return value;
}

function requireNativeInteger(value, label, { min = -Infinity, max = Infinity } = {}) {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new Error(`${label} must be a native safe integer`);
  }
  if (value < min || value > max) {
    throw new Error(`${label} must be within [${min}, ${max}]`);
  }
  return value;
}

function requireNumericVector(value, label, expectedLength = null) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  if (expectedLength != null && value.length !== expectedLength) {
    throw new Error(`${label} length ${value.length}; expected ${expectedLength}`);
  }
  return value.map((item, index) =>
    requireFiniteNumber(item, `${label}[${index}]`),
  );
}

function requireNumericMatrix(value, label, expectedRows, expectedColumns) {
  if (!Array.isArray(value) || value.length !== expectedRows) {
    throw new Error(`${label} rows ${value?.length}; expected ${expectedRows}`);
  }
  return value.map((row, index) =>
    requireNumericVector(row, `${label}[${index}]`, expectedColumns),
  );
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
      if (!Number.isFinite(output[j])) {
        throw new Error(`dense output ${j} is non-finite`);
      }
    }
  }
  for (let j = 0; j < output.length; j += 1) {
    if (!Number.isFinite(output[j])) {
      throw new Error(`dense output ${j} is non-finite`);
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
    if (!isPlainObject(model) || model.schema !== "haxlab-elite-js-runtime-v1") {
      throw new Error(
        `unsupported runtime model schema: ${model?.schema || "missing"}`,
      );
    }

    this.window = requireNativeInteger(model.window, "window", { min: 1 });

    if (!Array.isArray(model.base_input_columns) || model.base_input_columns.length === 0) {
      throw new Error("base_input_columns must be a non-empty array");
    }
    this.inputColumns = model.base_input_columns.map((value, index) => {
      if (typeof value !== "string" || value.trim() === "") {
        throw new Error(`base_input_columns[${index}] must be a non-empty string`);
      }
      return value;
    });
    if (new Set(this.inputColumns).size !== this.inputColumns.length) {
      throw new Error("base_input_columns must not contain duplicates");
    }

    if (
      model.feature_ordering != null &&
      (typeof model.feature_ordering !== "string" || model.feature_ordering.trim() === "")
    ) {
      throw new Error("feature_ordering must be a non-empty string when provided");
    }
    this.featureOrdering = model.feature_ordering || "nearest-distance-v1";

    this.mean = requireNumericVector(model.mean, "mean", this.inputColumns.length);
    this.std = requireNumericVector(model.std, "std", this.inputColumns.length);
    for (let index = 0; index < this.std.length; index += 1) {
      if (this.std[index] < 0) {
        throw new Error(`std[${index}] may not be negative`);
      }
    }

    if (model.role_ids != null && !isPlainObject(model.role_ids)) {
      throw new Error("role_ids must be an object when provided");
    }
    this.roleIds = { ...ROLE_IDS, ...(model.role_ids || {}) };
    for (const [role, value] of Object.entries(this.roleIds)) {
      requireNativeInteger(value, `role_ids.${role}`, { min: 0, max: 3 });
    }
    const canonicalRoleIds = ["gk", "dm", "am", "st"].map(
      (role) => this.roleIds[role],
    );
    if (new Set(canonicalRoleIds).size !== canonicalRoleIds.length) {
      throw new Error("canonical role_ids must map gk/dm/am/st to unique ids");
    }

    if (!Array.isArray(model.direction_classes) || model.direction_classes.length === 0) {
      throw new Error("direction_classes must be a non-empty array");
    }
    this.directionClasses = model.direction_classes.map((row, index) => {
      if (!isPlainObject(row)) {
        throw new Error(`direction_classes[${index}] must be an object`);
      }
      return {
        ...row,
        dir_x: requireFiniteNumber(row.dir_x, `direction_classes[${index}].dir_x`, {
          min: -1,
          max: 1,
        }),
        dir_y: requireFiniteNumber(row.dir_y, `direction_classes[${index}].dir_y`, {
          min: -1,
          max: 1,
        }),
      };
    });

    this.kickThreshold = requireFiniteNumber(model.kick_threshold, "kick_threshold", {
      min: 0,
      max: 1,
    });
    if (
      model.kick_thresholds_by_role != null &&
      !isPlainObject(model.kick_thresholds_by_role)
    ) {
      throw new Error("kick_thresholds_by_role must be an object when provided");
    }
    this.kickThresholdsByRole = Object.fromEntries(
      Object.entries(model.kick_thresholds_by_role || {}).map(([role, value]) => {
        if (!Object.prototype.hasOwnProperty.call(this.roleIds, role)) {
          throw new Error(`unknown kick threshold role: ${role}`);
        }
        return [
          role,
          requireFiniteNumber(value, `kick_thresholds_by_role.${role}`, {
            min: 0,
            max: 1,
          }),
        ];
      }),
    );
    this.kickMaxDistance = requireFiniteNumber(
      model.kick_max_distance ?? 31.0,
      "kick_max_distance",
      { min: 0 },
    );

    if (!isPlainObject(model.weights)) {
      throw new Error("weights must be an object");
    }
    const weights = model.weights;
    const inputDim = this.window * this.inputColumns.length + 4;

    const b1 = requireNumericVector(weights.b1, "weights.b1");
    if (b1.length === 0) throw new Error("weights.b1 may not be empty");
    const w1 = requireNumericMatrix(weights.w1, "weights.w1", inputDim, b1.length);

    const b2 = requireNumericVector(weights.b2, "weights.b2");
    if (b2.length === 0) throw new Error("weights.b2 may not be empty");
    const w2 = requireNumericMatrix(weights.w2, "weights.w2", b1.length, b2.length);

    const bd = requireNumericVector(
      weights.bd,
      "weights.bd",
      this.directionClasses.length,
    );
    const wd = requireNumericMatrix(
      weights.wd,
      "weights.wd",
      b2.length,
      bd.length,
    );

    const bk = requireNumericVector(weights.bk, "weights.bk", 1);
    const wk = requireNumericMatrix(weights.wk, "weights.wk", b2.length, 1);

    const hasWf = weights.wf != null;
    const hasBf = weights.bf != null;
    if (hasWf !== hasBf) {
      throw new Error("future head requires both weights.wf and weights.bf");
    }

    const normalizedWeights = { ...weights, w1, b1, w2, b2, wd, bd, wk, bk };
    if (hasWf) {
      normalizedWeights.bf = requireNumericVector(
        weights.bf,
        "weights.bf",
        this.directionClasses.length,
      );
      normalizedWeights.wf = requireNumericMatrix(
        weights.wf,
        "weights.wf",
        b2.length,
        normalizedWeights.bf.length,
      );
    }

    this.weights = normalizedWeights;
    this.futureHeadAvailable = hasWf;
    this.model = {
      ...model,
      base_input_columns: this.inputColumns.slice(),
      mean: this.mean.slice(),
      std: this.std.slice(),
      direction_classes: this.directionClasses.map((row) => ({ ...row })),
      kick_threshold: this.kickThreshold,
      kick_thresholds_by_role: { ...this.kickThresholdsByRole },
      kick_max_distance: this.kickMaxDistance,
      weights: this.weights,
    };
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
    const value = Number(this.roleIds[key]);
    if (!Number.isInteger(value) || value < 0 || value > 3) {
      throw new Error(`invalid configured role id: ${value}`);
    }
    return value;
  }

  vectorize(features) {
    if (!isPlainObject(features)) {
      throw new Error("features must be an object");
    }
    return this.inputColumns.map((name) => {
      if (!Object.prototype.hasOwnProperty.call(features, name)) {
        throw new Error(`missing model feature: ${name}`);
      }
      const value = features[name];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new Error(`model feature ${name} must be a finite native number`);
      }
      return value;
    });
  }

  normalizeFrame(frame) {
    return frame.map((value, index) => {
      const std = this.std[index];
      const normalized =
        (value - this.mean[index]) / (Math.abs(std) < 1e-12 ? 1 : std);
      if (!Number.isFinite(normalized)) {
        throw new Error(`normalized model feature ${this.inputColumns[index]} is non-finite`);
      }
      return normalized;
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
      ([, value]) => Number(value) === roleId,
    )?.[0];
    const roleKickThreshold = Number(
      this.kickThresholdsByRole[roleKey] ?? this.kickThreshold,
    );
    const ballDx = Number(features.ball_dx || 0);
    const ballDy = Number(features.ball_dy || 0);
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
      dir_x: Number(direction.dir_x),
      dir_y: Number(direction.dir_y),
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
      future_dir_x: Number(futureDirection.dir_x),
      future_dir_y: Number(futureDirection.dir_y),
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