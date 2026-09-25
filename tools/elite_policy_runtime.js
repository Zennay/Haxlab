"use strict";

const fs = require("fs");
const readline = require("readline");

const ROLE_IDS = Object.freeze({ gk: 0, dm: 1, am: 2, st: 3 });

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function sigmoid(value) {
  const clipped = clamp(value, -30, 30);
  return 1 / (1 + Math.exp(-clipped));
}

function softmax(logits) {
  let maxValue = -Infinity;
  for (const value of logits) {
    if (value > maxValue) maxValue = value;
  }
  const exp = new Array(logits.length);
  let total = 0;
  for (let i = 0; i < logits.length; i += 1) {
    const value = Math.exp(logits[i] - maxValue);
    exp[i] = value;
    total += value;
  }
  return exp.map((value) => value / total);
}

function dense(input, weights, bias, relu = false) {
  if (!Array.isArray(weights) || weights.length !== input.length) {
    throw new Error(
      `dense shape mismatch: input=${input.length}, weights=${weights?.length}`,
    );
  }
  const outputDim = bias.length;
  const output = bias.slice();
  for (let i = 0; i < input.length; i += 1) {
    const row = weights[i];
    if (!Array.isArray(row) || row.length !== outputDim) {
      throw new Error(
        `dense weight row ${i} has ${row?.length} values; expected ${outputDim}`,
      );
    }
    const value = input[i];
    if (value === 0) continue;
    for (let j = 0; j < outputDim; j += 1) {
      output[j] += value * row[j];
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
    this.model = model;
    this.window = Number(model.window);
    this.inputColumns = model.base_input_columns.slice();
    this.mean = model.mean.map(Number);
    this.std = model.std.map(Number);
    this.roleIds = { ...ROLE_IDS, ...(model.role_ids || {}) };
    this.directionClasses = model.direction_classes.slice();
    this.kickThreshold = Number(model.kick_threshold);
    this.weights = model.weights;
    this.history = new Map();

    if (!Number.isInteger(this.window) || this.window < 1) {
      throw new Error(`invalid window: ${model.window}`);
    }
    if (this.mean.length !== this.inputColumns.length) {
      throw new Error("mean/input column length mismatch");
    }
    if (this.std.length !== this.inputColumns.length) {
      throw new Error("std/input column length mismatch");
    }
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
    return this.inputColumns.map((name) => {
      if (!Object.prototype.hasOwnProperty.call(features, name)) {
        throw new Error(`missing model feature: ${name}`);
      }
      const value = Number(features[name]);
      if (!Number.isFinite(value)) {
        throw new Error(`non-finite model feature ${name}: ${features[name]}`);
      }
      return value;
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
    let directionClass = 0;
    for (let i = 1; i < directionProbabilities.length; i += 1) {
      if (
        directionProbabilities[i] >
        directionProbabilities[directionClass]
      ) {
        directionClass = i;
      }
    }

    const kickLogit = dense(
      h2,
      this.weights.wk,
      this.weights.bk,
      false,
    )[0];
    const kickProbability = sigmoid(kickLogit);
    const direction = this.directionClasses[directionClass];
    if (!direction) {
      throw new Error(`direction class ${directionClass} missing from model`);
    }

    return {
      agent_id: agentId,
      role: String(role).toLowerCase(),
      role_id: roleId,
      dir_x: Number(direction.dir_x),
      dir_y: Number(direction.dir_y),
      kick: kickProbability >= this.kickThreshold,
      kick_probability: kickProbability,
      kick_threshold: this.kickThreshold,
      direction_class: directionClass,
      direction_probability: directionProbabilities[directionClass],
      history_frames: frames.length,
      window: this.window,
    };
  }

  info() {
    return {
      schema: this.model.schema,
      source_model_schema: this.model.source_model_schema,
      window: this.window,
      kick_threshold: this.kickThreshold,
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
