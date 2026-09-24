#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const initAPI = require("node-haxball");

const API = initAPI();
const { Replay, Utils } = API;

function usage() {
  console.error("Usage: node tools/decode_replay.js <replay.hbr2> [sampleEveryTicks]");
  process.exit(2);
}

const replayPath = process.argv[2];
if (!replayPath) usage();

const sampleEvery = Math.max(1, Number.parseInt(process.argv[3] || "6", 10) || 6);
const data = fs.readFileSync(replayPath);

const replayData = Replay.readAll(data);

const players = new Map();
const teamGoals = { red: 0, blue: 0, other: 0 };
const ball = {
  samples: 0,
  sumX: 0,
  sumY: 0,
  sumSpeed: 0,
  heatmap: Object.create(null),
};

let tickCount = 0;
let sampledStateCount = 0;
let framesAdvanced = 0;
let gameTicks = 0;
let gameStarts = 0;
let gameStops = 0;
let teamGoalEvents = 0;

function heatKey(x, y, size = 20) {
  return `${Math.floor(x / size)}:${Math.floor(y / size)}`;
}

function bump(obj, key, amount = 1) {
  obj[key] = (obj[key] || 0) + amount;
}

function ensurePlayer(id, fallback = null) {
  if (id == null) return null;
  const numericId = Number(id);
  let value = players.get(numericId);
  if (!value) {
    value = {
      id: numericId,
      name: fallback?.name ?? null,
      teamId: fallback?.team?.id ?? null,
      samples: 0,
      sumX: 0,
      sumY: 0,
      nearestBallSamples: 0,
      closeBallSamples: 0,
      inputEvents: 0,
      kickEvents: 0,
      kickPressedInputs: 0,
      directionHistogram: Object.create(null),
      heatmap: Object.create(null),
    };
    players.set(numericId, value);
  } else {
    if (value.name == null && fallback?.name != null) value.name = fallback.name;
    if (fallback?.team?.id != null) value.teamId = fallback.team.id;
  }
  return value;
}

for (const player of replayData.roomData?.players || []) {
  ensurePlayer(player.id, player);
}

let reader = null;
let settled = false;
let timeout = null;

function finish(resolve, reject, error = null) {
  if (settled) return;
  settled = true;
  if (timeout) clearTimeout(timeout);
  try {
    framesAdvanced = Math.max(framesAdvanced, reader?.getCurrentFrameNo?.() || 0);
  } catch (_) {}
  try {
    reader?.destroy?.();
  } catch (_) {}
  if (error) reject(error);
  else resolve();
}

function sampleState() {
  if (!reader) return;
  const gameState = reader.gameState;
  const statePlayers = reader.state?.players || [];
  if (!gameState?.physicsState?.discs?.length) return;

  gameTicks += 1;
  if ((reader.getCurrentFrameNo() % sampleEvery) !== 0) return;

  const ballDisc = gameState.physicsState.discs[0];
  if (!ballDisc?.pos) return;

  sampledStateCount += 1;
  const bx = Number(ballDisc.pos.x);
  const by = Number(ballDisc.pos.y);
  const bsx = Number(ballDisc.speed?.x || 0);
  const bsy = Number(ballDisc.speed?.y || 0);

  ball.samples += 1;
  ball.sumX += bx;
  ball.sumY += by;
  ball.sumSpeed += Math.hypot(bsx, bsy);
  bump(ball.heatmap, heatKey(bx, by));

  let nearest = null;
  let nearestDist2 = Infinity;

  for (const player of statePlayers) {
    const aggregate = ensurePlayer(player.id, player);
    const disc = player.disc;
    if (!aggregate || !disc?.pos) continue;

    aggregate.teamId = player.team?.id ?? aggregate.teamId;
    const x = Number(disc.pos.x);
    const y = Number(disc.pos.y);
    aggregate.samples += 1;
    aggregate.sumX += x;
    aggregate.sumY += y;
    bump(aggregate.heatmap, heatKey(x, y));

    const dx = x - bx;
    const dy = y - by;
    const d2 = dx * dx + dy * dy;
    if (d2 < nearestDist2) {
      nearestDist2 = d2;
      nearest = aggregate;
    }
  }

  if (nearest) {
    nearest.nearestBallSamples += 1;
    if (nearestDist2 <= 30 * 30) nearest.closeBallSamples += 1;
  }
}

function runReplay() {
  return new Promise((resolve, reject) => {
    const callbacks = {
      onGameTick: () => {
        tickCount += 1;
        sampleState();
      },
      onPlayerJoin: (playerObj) => {
        ensurePlayer(playerObj?.id, playerObj);
      },
      onPlayerTeamChange: (id) => {
        const playerObj = reader?.state?.players?.find((p) => p.id === id);
        ensurePlayer(id, playerObj);
      },
      onPlayerInputChange: (id, input) => {
        const p = ensurePlayer(id);
        if (!p) return;
        p.inputEvents += 1;
        try {
          const parsed = Utils.reverseKeyState(input);
          const dirX = parsed?.dirX ?? 0;
          const dirY = parsed?.dirY ?? 0;
          const kick = Boolean(parsed?.kick);
          bump(p.directionHistogram, `${dirX}:${dirY}`);
          if (kick) p.kickPressedInputs += 1;
        } catch (_) {}
      },
      onPlayerBallKick: (id) => {
        const p = ensurePlayer(id);
        if (p) p.kickEvents += 1;
      },
      onTeamGoal: (teamId) => {
        teamGoalEvents += 1;
        if (teamId === 1) teamGoals.red += 1;
        else if (teamId === 2) teamGoals.blue += 1;
        else teamGoals.other += 1;
      },
      onGameStart: () => {
        gameStarts += 1;
      },
      onGameStop: () => {
        gameStops += 1;
      },
    };

    const fastRAF = (callback) => setImmediate(() => callback(Date.now()));
    const fastCancelRAF = (handle) => clearImmediate(handle);

    reader = Replay.read(data, callbacks, {
      requestAnimationFrame: fastRAF,
      cancelAnimationFrame: fastCancelRAF,
    });

    reader.onEnd = () => finish(resolve, reject);

    timeout = setTimeout(
      () => finish(resolve, reject, new Error("decoder_timeout")),
      120000,
    );

    // IMPORTANT: do not use setCurrentFrameNo() here. node-haxball intentionally
    // detaches gameplay callbacks while seeking, which would yield event metadata
    // without reconstructed game-tick/state observations. Fast playback keeps the
    // callbacks attached while still running as quickly as CPU allows.
    reader.setSpeed(100000);
  });
}

(async () => {
  await runReplay();

  const playerRows = Array.from(players.values())
    .sort((a, b) => a.id - b.id)
    .map((p) => ({
      ...p,
      averageX: p.samples ? p.sumX / p.samples : null,
      averageY: p.samples ? p.sumY / p.samples : null,
    }));

  const eventTypeCounts = Object.create(null);
  for (const event of replayData.events || []) {
    bump(eventTypeCounts, String(event.eventType));
  }

  const output = {
    schemaVersion: 2,
    decoder: "node-haxball@2.3.1",
    sourceFile: path.basename(replayPath),
    version: replayData.version,
    totalFrames: replayData.totalFrames,
    rawEventCount: replayData.events?.length || 0,
    goalMarkerCount: replayData.goalMarkers?.length || 0,
    eventTypeCounts,
    simulation: {
      tickCount,
      framesAdvanced,
      gameTicks,
      sampleEveryTicks: sampleEvery,
      sampledStateCount,
      gameStarts,
      gameStops,
      teamGoalEvents,
      teamGoals,
    },
    ball: {
      ...ball,
      averageX: ball.samples ? ball.sumX / ball.samples : null,
      averageY: ball.samples ? ball.sumY / ball.samples : null,
      averageSpeed: ball.samples ? ball.sumSpeed / ball.samples : null,
    },
    players: playerRows,
  };

  process.stdout.write(JSON.stringify(output));
})().catch((error) => {
  console.error(error?.stack || String(error));
  process.exit(1);
});
