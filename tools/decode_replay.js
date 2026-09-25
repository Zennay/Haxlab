#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const initAPI = require("node-haxball");

const API = initAPI();
const { Replay, Utils } = API;

function formatFatal(error) {
  if (error == null) return "<null>";
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.stack || error.message || String(error);
  try {
    return JSON.stringify(error);
  } catch (_) {
    return String(error);
  }
}

process.on("uncaughtException", (error) => {
  console.error("HAXLAB_UNCAUGHT:", formatFatal(error));
  process.exit(1);
});

process.on("unhandledRejection", (error) => {
  console.error("HAXLAB_UNHANDLED_REJECTION:", formatFatal(error));
  process.exit(1);
});

function usage() {
  console.error("Usage: node tools/decode_replay.js <replay.hbr2> [sampleEveryTicks]");
  process.exit(2);
}

const replayPath = process.argv[2];
if (!replayPath) usage();

const sampleEvery = Math.max(1, Number.parseInt(process.argv[3] || "6", 10) || 6);
// node-haxball currently constructs DataView from data.buffer and does not
// account for Buffer.byteOffset. Small Node Buffers are often slices of the
// shared <4 KiB pool, so passing fs.readFileSync() directly can make a valid
// HBR2 file appear to have the wrong magic. Force a tightly-backed Uint8Array.
const data = Uint8Array.from(fs.readFileSync(replayPath));

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
let lastKick = null;
let kickHistory = [];

function heatKey(x, y, size = 20) {
  return `${Math.floor(x / size)}:${Math.floor(y / size)}`;
}

function bump(obj, key, amount = 1) {
  obj[key] = (obj[key] || 0) + amount;
}

function attackAxisX(teamId, x) {
  if (teamId === 1) return x;
  if (teamId === 2) return -x;
  return 0;
}

function currentKickSnapshot(id) {
  const playerObj = reader?.state?.players?.find((p) => p.id === id);
  const teamId = playerObj?.team?.id ?? null;
  const ballDisc = reader?.gameState?.physicsState?.discs?.[0];
  const frameNo = reader?.getCurrentFrameNo?.() ?? 0;
  return {
    playerId: Number(id),
    teamId,
    frameNo: Number(frameNo),
    ballX: ballDisc?.pos ? Number(ballDisc.pos.x) : null,
    ballY: ballDisc?.pos ? Number(ballDisc.pos.y) : null,
  };
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
      inferredRetainedChains: 0,
      inferredLostChains: 0,
      inferredRecoveries: 0,
      inferredGoals: 0,
      inferredAssists: 0,
      progressionEvents: 0,
      progressionSum: 0,
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
        const playerObj = reader?.state?.players?.find((p) => p.id === id);
        const p = ensurePlayer(id, playerObj);
        if (!p) return;
        p.kickEvents += 1;

        const kick = currentKickSnapshot(id);
        p.teamId = kick.teamId ?? p.teamId;

        if (
          lastKick &&
          lastKick.teamId != null &&
          kick.teamId != null &&
          (lastKick.teamId === 1 || lastKick.teamId === 2) &&
          (kick.teamId === 1 || kick.teamId === 2)
        ) {
          const previous = ensurePlayer(lastKick.playerId);
          if (previous) {
            if (lastKick.teamId === kick.teamId) {
              previous.inferredRetainedChains += 1;
              if (lastKick.ballX != null && kick.ballX != null) {
                previous.progressionEvents += 1;
                previous.progressionSum +=
                  attackAxisX(lastKick.teamId, kick.ballX) -
                  attackAxisX(lastKick.teamId, lastKick.ballX);
              }
            } else {
              previous.inferredLostChains += 1;
              p.inferredRecoveries += 1;
            }
          }
        }

        lastKick = kick;
        kickHistory.push(kick);
        if (kickHistory.length > 12) kickHistory.shift();
      },
      onTeamGoal: (teamId) => {
        teamGoalEvents += 1;
        if (teamId === 1) teamGoals.red += 1;
        else if (teamId === 2) teamGoals.blue += 1;
        else teamGoals.other += 1;

        const goalFrame = Number(reader?.getCurrentFrameNo?.() ?? 0);
        let scorerKick = null;
        let assistKick = null;

        for (let i = kickHistory.length - 1; i >= 0; i -= 1) {
          const kick = kickHistory[i];
          if (goalFrame - kick.frameNo > 600) break;
          if (kick.teamId !== teamId) {
            if (scorerKick) break;
            continue;
          }

          if (!scorerKick && goalFrame - kick.frameNo <= 300) {
            scorerKick = kick;
            continue;
          }

          if (
            scorerKick &&
            kick.playerId !== scorerKick.playerId &&
            goalFrame - kick.frameNo <= 600
          ) {
            assistKick = kick;
            break;
          }
        }

        if (scorerKick) {
          const scorer = ensurePlayer(scorerKick.playerId);
          if (scorer) scorer.inferredGoals += 1;
        }
        if (assistKick) {
          const assister = ensurePlayer(assistKick.playerId);
          if (assister) assister.inferredAssists += 1;
        }

        lastKick = null;
        kickHistory = [];
      },
      onGameStart: () => {
        gameStarts += 1;
        lastKick = null;
        kickHistory = [];
      },
      onGameStop: () => {
        gameStops += 1;
        lastKick = null;
        kickHistory = [];
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
      inferredRetentionRate:
        (p.inferredRetainedChains + p.inferredLostChains) > 0
          ? p.inferredRetainedChains /
            (p.inferredRetainedChains + p.inferredLostChains)
          : null,
      averageProgression:
        p.progressionEvents > 0 ? p.progressionSum / p.progressionEvents : null,
    }));

  const eventTypeCounts = Object.create(null);
  for (const event of replayData.events || []) {
    bump(eventTypeCounts, String(event.eventType));
  }

  const output = {
    schemaVersion: 3,
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
