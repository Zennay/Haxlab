"use strict";

const fs = require("fs");
const path = require("path");

const TRACE_SCHEMA = "haxlab-recovery-trace-v1";
const TRACE_ROW_SCHEMA = "haxlab-recovery-trace-row-v1";

function positionSnapshot(position) {
  if (!position) return null;
  return { x: Number(position.x || 0), y: Number(position.y || 0) };
}

function compactCanonical(canonical) {
  if (!canonical || typeof canonical !== "object") return null;
  const result = {};
  for (const key of [
    "dir_x", "dir_y", "kick", "kick_probability", "direction_index",
    "ood_max_abs_z",
  ]) {
    if (canonical[key] !== undefined) result[key] = canonical[key];
  }
  return result;
}

function failureLabels(diagnostics, formation, mode) {
  const labels = [];
  if (diagnostics?.boundary) labels.push("boundary");
  if (Number(diagnostics?.role_deviation || 0) >= 450) {
    labels.push("role_deviation");
  }
  if (diagnostics?.far_stall) labels.push("far_stall");
  if (diagnostics?.context_miss) labels.push("context_miss");
  if (Number(diagnostics?.ood_max_abs_z || 0) > 8) labels.push("ood");
  if (mode === "full_team" && formation?.collapsed) {
    labels.push("team_shape_collapse");
  }
  if (mode === "full_team" && formation?.overstretched) {
    labels.push("team_shape_overstretched");
  }
  return labels;
}

class RecoveryTraceRecorder {
  constructor({
    enabled = false,
    mode,
    testedRole = null,
    scenarioIndex,
    testTeamId,
    repeatIndex = 0,
    seed,
    maxPerFailure = 4,
    minGapTicks = 120,
    historySamples = 4,
  }) {
    this.enabled = Boolean(enabled);
    this.mode = mode;
    this.testedRole = testedRole;
    this.scenarioIndex = Number(scenarioIndex);
    this.testTeamId = Number(testTeamId);
    this.repeatIndex = Number(repeatIndex);
    this.seed = Number(seed);
    this.maxPerFailure = Math.max(1, Number(maxPerFailure) || 4);
    this.minGapTicks = Math.max(0, Number(minGapTicks) || 0);
    this.historySamples = Math.max(0, Number(historySamples) || 0);
    this.pending = [];
    this.history = new Map();
    this.counts = new Map();
    this.lastTick = new Map();
    this.rows = [];
  }

  observe(observation) {
    if (!this.enabled) return;
    this.pending.push(observation);
  }

  _frame(observation, tick) {
    return {
      tick: Number(tick),
      features: observation.features,
      action: {
        dir_x: Number(observation.action?.dirX || 0),
        dir_y: Number(observation.action?.dirY || 0),
        kick: Boolean(observation.action?.kick),
      },
      canonical_action: compactCanonical(observation.canonical),
      context: observation.diagnostics?.context || null,
      player_position: positionSnapshot(observation.playerPosition),
      ball_position: positionSnapshot(observation.ballPosition),
    };
  }

  _eligible(botKey, labels, tick) {
    const eligible = [];
    for (const label of labels) {
      const key = botKey + ":" + label;
      const count = Number(this.counts.get(key) || 0);
      const last = this.lastTick.has(key) ? Number(this.lastTick.get(key)) : null;
      const eventLike = label === "context_miss";
      if (count >= this.maxPerFailure) continue;
      if (!eventLike && last != null && Number(tick) - last < this.minGapTicks) {
        continue;
      }
      this.counts.set(key, count + 1);
      this.lastTick.set(key, Number(tick));
      eligible.push(label);
    }
    return eligible;
  }

  finishTick({ tick, formation = null }) {
    if (!this.enabled) return;
    for (const observation of this.pending) {
      const bot = observation.bot || {};
      const botKey = String(bot.id);
      const frame = this._frame(observation, tick);
      const history = this.history.get(botKey) || [];

      if (bot.modelKind === "challenger") {
        const labels = failureLabels(
          observation.diagnostics,
          formation,
          this.mode,
        );
        const eligible = this._eligible(botKey, labels, tick);
        if (eligible.length) {
          this.rows.push({
            schema: TRACE_ROW_SCHEMA,
            mode: this.mode,
            tested_role: this.testedRole,
            role: bot.role,
            scenario_index: this.scenarioIndex,
            test_team_id: this.testTeamId,
            repeat_index: this.repeatIndex,
            seed: this.seed,
            tick: Number(tick),
            failure_types: eligible.sort(),
            diagnostics: {
              role_deviation: observation.diagnostics?.role_deviation ?? null,
              role_target: observation.diagnostics?.role_target ?? null,
              boundary: Boolean(observation.diagnostics?.boundary),
              far_stall: Boolean(observation.diagnostics?.far_stall),
              context_miss: Boolean(observation.diagnostics?.context_miss),
              ood_max_abs_z: Number(
                observation.diagnostics?.ood_max_abs_z || 0,
              ),
              team_shape: formation,
            },
            recovery_window: [...history.slice(-this.historySamples), frame],
          });
        }
      }

      history.push(frame);
      if (history.length > this.historySamples) history.shift();
      this.history.set(botKey, history);
    }
    this.pending = [];
  }
}

function writeRecoveryTrace(filePath, header, rows) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const first = JSON.stringify({ schema: TRACE_SCHEMA, ...header });
  const body = rows.map((row) => JSON.stringify(row));
  fs.writeFileSync(filePath, [first, ...body].join("\n") + "\n");
}

module.exports = {
  TRACE_SCHEMA,
  TRACE_ROW_SCHEMA,
  RecoveryTraceRecorder,
  failureLabels,
  writeRecoveryTrace,
};