#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const initAPI = require("node-haxball");
const { buildFeatureObject, num } = require("./elite_features");

const API = initAPI();
const { Replay } = API;
const ROLES = ["gk", "dm", "am", "st"];

function usage() {
  console.error(
    "Usage: node tools/extract_replay_scenarios.js " +
      "<replay.hbr2> <analysis.json> <output.json> " +
      "[maxScenarios=16] [historyWindow=8] [sampleEveryTicks=6]",
  );
  process.exit(2);
}

const replayPath = process.argv[2];
const analysisPath = process.argv[3];
const outputPath = process.argv[4];
const maxScenarios = Math.max(2, Number.parseInt(process.argv[5] || "16", 10));
const historyWindow = Math.max(2, Number.parseInt(process.argv[6] || "8", 10));
const sampleEvery = Math.max(1, Number.parseInt(process.argv[7] || "6", 10));
if (!replayPath || !analysisPath || !outputPath) usage();

const analysis = JSON.parse(fs.readFileSync(analysisPath, "utf8"));
const replayBytes = Uint8Array.from(fs.readFileSync(replayPath));
const replayData = Replay.readAll(replayBytes);
const teamGoals = { 1: 0, 2: 0 };

function roleMapFromAnalysis(payload) {
  const byTeam = { 1: [], 2: [] };
  for (const player of payload.players || []) {
    const teamId = Number(player.teamId || 0);
    if (!(teamId === 1 || teamId === 2)) continue;
    if (player.id == null || player.averageX == null || Number(player.samples || 0) <= 0) {
      continue;
    }
    const sign = teamId === 1 ? 1 : -1;
    byTeam[teamId].push({
      id: Number(player.id),
      name: player.name || null,
      samples: Number(player.samples || 0),
      attackX: sign * Number(player.averageX),
    });
  }

  const result = { 1: {}, 2: {} };
  for (const teamId of [1, 2]) {
    const core = byTeam[teamId]
      .sort((a, b) => b.samples - a.samples)
      .slice(0, 4)
      .sort((a, b) => a.attackX - b.attackX);
    if (core.length !== 4) {
      throw new Error("analysis does not contain four stable players for team " + teamId);
    }
    for (let index = 0; index < 4; index += 1) {
      result[teamId][ROLES[index]] = core[index];
    }
  }
  return result;
}

const roleMap = roleMapFromAnalysis(analysis);

function discSnapshot(disc) {
  return {
    x: num(disc?.pos?.x),
    y: num(disc?.pos?.y),
    vx: num(disc?.speed?.x),
    vy: num(disc?.speed?.y),
  };
}

function eligibleOpenPlay(ball) {
  if (!ball?.pos) return false;
  const speed = Math.hypot(num(ball.speed?.x), num(ball.speed?.y));
  const centerDistance = Math.hypot(num(ball.pos.x), num(ball.pos.y));
  return speed >= 0.15 || centerDistance >= 35;
}

const history = [];
const candidates = [];
let reader = null;
let settled = false;
let timeout = null;
let framesAdvanced = 0;

function captureFrame() {
  const frame = Number(reader?.getCurrentFrameNo?.() || 0);
  if (frame % sampleEvery !== 0) return;

  const gameState = reader?.gameState;
  const players = reader?.state?.players || [];
  const ball = gameState?.physicsState?.discs?.[0];
  if (!gameState || !ball?.pos) return;

  const byId = new Map(players.map((player) => [Number(player.id), player]));
  const teams = { 1: {}, 2: {} };
  const features = { 1: {}, 2: {} };

  for (const teamId of [1, 2]) {
    for (const role of ROLES) {
      const source = roleMap[teamId][role];
      const player = byId.get(source.id);
      if (!player?.disc?.pos || Number(player.team?.id || 0) !== teamId) {
        return;
      }
      teams[teamId][role] = {
        source_player_id: source.id,
        source_name: source.name,
        ...discSnapshot(player.disc),
      };
      const feature = buildFeatureObject(
        player,
        { players },
        gameState,
      );
      if (!feature) return;
      feature.score_diff =
        Number(teamGoals[teamId] || 0) - Number(teamGoals[3 - teamId] || 0);
      features[teamId][role] = feature;
    }
  }

  const row = {
    frame,
    score: { red: teamGoals[1], blue: teamGoals[2] },
    ball: discSnapshot(ball),
    teams,
    features,
  };
  history.push(row);
  while (history.length > historyWindow) history.shift();

  if (
    history.length === historyWindow &&
    eligibleOpenPlay(ball)
  ) {
    candidates.push({
      frame,
      score: row.score,
      ball: row.ball,
      teams: row.teams,
      history: history.map((item) => ({
        frame: item.frame,
        features: item.features,
      })),
    });
  }
}

function chooseEvenly(rows, count) {
  if (rows.length <= count) return rows;
  const chosen = [];
  const used = new Set();
  for (let i = 0; i < count; i += 1) {
    const index = Math.round(i * (rows.length - 1) / Math.max(1, count - 1));
    if (!used.has(index)) {
      used.add(index);
      chosen.push(rows[index]);
    }
  }
  return chosen;
}

function finish(resolve, reject, error = null) {
  if (settled) return;
  settled = true;
  if (timeout) clearTimeout(timeout);
  try {
    framesAdvanced = Math.max(
      framesAdvanced,
      Number(reader?.getCurrentFrameNo?.() || 0),
    );
  } catch (_) {}
  try { reader?.destroy?.(); } catch (_) {}
  if (error) reject(error);
  else resolve();
}

function runReplay() {
  return new Promise((resolve, reject) => {
    const callbacks = {
      onGameTick: captureFrame,
      onTeamGoal: (teamId) => {
        if (teamId === 1 || teamId === 2) teamGoals[teamId] += 1;
      },
      onGameStart: () => {
        teamGoals[1] = 0;
        teamGoals[2] = 0;
        history.length = 0;
      },
    };
    const fastRAF = (callback) => setImmediate(() => callback(Date.now()));
    const fastCancelRAF = (handle) => clearImmediate(handle);
    reader = Replay.read(replayBytes, callbacks, {
      requestAnimationFrame: fastRAF,
      cancelAnimationFrame: fastCancelRAF,
    });
    reader.onEnd = () => finish(resolve, reject);
    timeout = setTimeout(
      () => finish(resolve, reject, new Error("scenario_extract_timeout")),
      120000,
    );
    reader.setSpeed(100000);
  });
}

(async () => {
  try {
    await runReplay();
    const scenarios = chooseEvenly(candidates, maxScenarios);
    if (!scenarios.length) {
      throw new Error("no usable 4v4 open-play scenarios found");
    }
    const result = {
      schema: "haxlab-replay-seeded-scenarios-v1",
      source_replay: replayPath,
      source_analysis: analysisPath,
      total_frames: replayData.totalFrames,
      frames_advanced: framesAdvanced,
      sample_every_ticks: sampleEvery,
      history_window: historyWindow,
      candidate_count: candidates.length,
      scenario_count: scenarios.length,
      role_map: roleMap,
      scenarios,
    };
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + "\n");
    process.stdout.write(JSON.stringify({
      schema: result.schema,
      scenario_count: result.scenario_count,
      candidate_count: result.candidate_count,
      history_window: result.history_window,
      output_path: outputPath,
    }) + "\n");
  } catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
})();
