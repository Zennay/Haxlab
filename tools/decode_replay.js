#!/usr/bin/env node
"use strict";

const fs = require("fs");
const crypto = require("crypto");
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
let lastContact = null;
let lastTouch = null;
let touchHistory = [];
let pendingKick = null;

const TOUCH_GAP_FRAMES = 3;
const KICK_OUTCOME_WINDOW_FRAMES = 240;
const GOAL_TOUCH_WINDOW_FRAMES = 300;
const ASSIST_TOUCH_WINDOW_FRAMES = 600;
const UNDER_PRESSURE_DISTANCE = 45;

function heatKey(x, y, size = 20) {
  return `${Math.floor(x / size)}:${Math.floor(y / size)}`;
}

function bump(obj, key, amount = 1) {
  obj[key] = (obj[key] || 0) + amount;
}

function authHash(value) {
  if (value == null || value === "") return null;
  return crypto
    .createHash("sha256")
    .update(String(value))
    .digest("hex")
    .slice(0, 20);
}

function attackAxisX(teamId, x) {
  if (teamId === 1) return x;
  if (teamId === 2) return -x;
  return 0;
}

function nearestOpponentDistance(playerId, teamId) {
  if (!(teamId === 1 || teamId === 2)) return null;
  const statePlayers = reader?.state?.players || [];
  const playerObj = statePlayers.find((p) => p.id === playerId);
  const ownPos = playerObj?.disc?.pos;
  if (!ownPos) return null;

  let best = Infinity;
  for (const opponent of statePlayers) {
    if (opponent.id === playerId || opponent.team?.id === teamId) continue;
    if (!(opponent.team?.id === 1 || opponent.team?.id === 2)) continue;
    const pos = opponent.disc?.pos;
    if (!pos) continue;
    const distance = Math.hypot(
      Number(pos.x) - Number(ownPos.x),
      Number(pos.y) - Number(ownPos.y),
    );
    if (distance < best) best = distance;
  }
  return Number.isFinite(best) ? best : null;
}

function currentKickSnapshot(id) {
  const playerObj = reader?.state?.players?.find((p) => p.id === id);
  const teamId = playerObj?.team?.id ?? null;
  const ballDisc = reader?.gameState?.physicsState?.discs?.[0];
  const frameNo = reader?.getCurrentFrameNo?.() ?? 0;
  const pressureDistance = nearestOpponentDistance(Number(id), teamId);
  return {
    playerId: Number(id),
    teamId,
    frameNo: Number(frameNo),
    ballX: ballDisc?.pos ? Number(ballDisc.pos.x) : null,
    ballY: ballDisc?.pos ? Number(ballDisc.pos.y) : null,
    pressureDistance,
    underPressure:
      pressureDistance != null && pressureDistance <= UNDER_PRESSURE_DISTANCE,
  };
}

function currentTouchSnapshot(id) {
  const playerObj = reader?.state?.players?.find((p) => p.id === id);
  const teamId = playerObj?.team?.id ?? null;
  const ballDisc = reader?.gameState?.physicsState?.discs?.[0];
  const frameNo = Number(reader?.getCurrentFrameNo?.() ?? 0);
  const pressureDistance = nearestOpponentDistance(Number(id), teamId);
  return {
    playerId: Number(id),
    teamId,
    frameNo,
    ballX: ballDisc?.pos ? Number(ballDisc.pos.x) : null,
    ballY: ballDisc?.pos ? Number(ballDisc.pos.y) : null,
    pressureDistance,
    underPressure:
      pressureDistance != null && pressureDistance <= UNDER_PRESSURE_DISTANCE,
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
      authHash: authHash(fallback?.auth),
      teamId: fallback?.team?.id ?? null,
      samples: 0,
      sumX: 0,
      sumY: 0,
      nearestBallSamples: 0,
      closeBallSamples: 0,
      inputEvents: 0,
      kickEvents: 0,
      kickPressedInputs: 0,
      ballCollisionEvents: 0,
      touches: 0,
      selfRetouches: 0,
      teamTouchTransfersOut: 0,
      teamTouchReceipts: 0,
      turnovers: 0,
      recoveries: 0,
      kickTransfersToTeammate: 0,
      kickTransfersToOpponent: 0,
      kickSelfRetouches: 0,
      receivedKickTransfers: 0,
      interceptedKickTransfers: 0,
      pressureObservations: 0,
      pressureDistanceSum: 0,
      underPressureTouches: 0,
      underPressureKickEvents: 0,
      pressuredTransitions: 0,
      retainedUnderPressure: 0,
      lostUnderPressure: 0,
      touchProgressionEvents: 0,
      touchProgressionSum: 0,
      touchGoals: 0,
      touchAssists: 0,
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
    if (value.authHash == null && fallback?.auth != null) {
      value.authHash = authHash(fallback.auth);
    }
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


function recordBallTouch(playerId) {
  const playerObj = reader?.state?.players?.find((p) => p.id === playerId);
  const aggregate = ensurePlayer(playerId, playerObj);
  if (!aggregate) return;

  aggregate.ballCollisionEvents += 1;

  // Collapse repeated collision callbacks before calculating pressure context.
  // Continuous ball contact can emit a callback on successive physics frames.
  const frameNo = Number(reader?.getCurrentFrameNo?.() ?? 0);
  if (
    lastContact &&
    lastContact.playerId === Number(playerId) &&
    frameNo - lastContact.frameNo <= TOUCH_GAP_FRAMES
  ) {
    lastContact.frameNo = frameNo;
    return;
  }
  lastContact = { playerId: Number(playerId), frameNo };

  const touch = currentTouchSnapshot(playerId);
  aggregate.teamId = touch.teamId ?? aggregate.teamId;
  if (!(touch.teamId === 1 || touch.teamId === 2)) return;

  aggregate.touches += 1;
  if (touch.pressureDistance != null) {
    aggregate.pressureObservations += 1;
    aggregate.pressureDistanceSum += touch.pressureDistance;
    if (touch.underPressure) aggregate.underPressureTouches += 1;
  }

  const previous = lastTouch;
  if (previous) {
    const previousPlayer = ensurePlayer(previous.playerId);
    if (previous.playerId === touch.playerId) {
      aggregate.selfRetouches += 1;
    } else if (previousPlayer) {
      const sameTeam =
        previous.teamId != null &&
        touch.teamId != null &&
        previous.teamId === touch.teamId;

      if (sameTeam) {
        previousPlayer.teamTouchTransfersOut += 1;
        aggregate.teamTouchReceipts += 1;
      } else {
        previousPlayer.turnovers += 1;
        aggregate.recoveries += 1;
      }

      if (previous.ballX != null && touch.ballX != null) {
        previousPlayer.touchProgressionEvents += 1;
        previousPlayer.touchProgressionSum +=
          attackAxisX(previous.teamId, touch.ballX) -
          attackAxisX(previous.teamId, previous.ballX);
      }

      if (previous.underPressure) {
        previousPlayer.pressuredTransitions += 1;
        if (sameTeam) previousPlayer.retainedUnderPressure += 1;
        else previousPlayer.lostUnderPressure += 1;
      }
    }
  }

  if (pendingKick) {
    const age = touch.frameNo - pendingKick.frameNo;
    if (age > KICK_OUTCOME_WINDOW_FRAMES) {
      pendingKick = null;
    } else if (age >= 0) {
      const kicker = ensurePlayer(pendingKick.playerId);
      if (kicker) {
        if (
          touch.playerId === pendingKick.playerId &&
          age > TOUCH_GAP_FRAMES
        ) {
          kicker.kickSelfRetouches += 1;
          pendingKick = null;
        } else if (touch.playerId !== pendingKick.playerId) {
          if (touch.teamId === pendingKick.teamId) {
            kicker.kickTransfersToTeammate += 1;
            aggregate.receivedKickTransfers += 1;
          } else {
            kicker.kickTransfersToOpponent += 1;
            aggregate.interceptedKickTransfers += 1;
          }
          pendingKick = null;
        }
      }
    }
  }

  lastTouch = touch;
  if (
    touchHistory.length > 0 &&
    touchHistory[touchHistory.length - 1].playerId === touch.playerId
  ) {
    touchHistory[touchHistory.length - 1] = touch;
  } else {
    touchHistory.push(touch);
    if (touchHistory.length > 24) touchHistory.shift();
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
      onCollisionDiscVsDisc: (
        discId1,
        discPlayerId1,
        discId2,
        discPlayerId2,
      ) => {
        if (discId1 === 0 && discPlayerId2 != null) {
          recordBallTouch(discPlayerId2);
        } else if (discId2 === 0 && discPlayerId1 != null) {
          recordBallTouch(discPlayerId1);
        }
      },
      onPlayerBallKick: (id) => {
        const playerObj = reader?.state?.players?.find((p) => p.id === id);
        const p = ensurePlayer(id, playerObj);
        if (!p) return;
        p.kickEvents += 1;

        const kick = currentKickSnapshot(id);
        p.teamId = kick.teamId ?? p.teamId;
        if (kick.underPressure) p.underPressureKickEvents += 1;
        pendingKick = kick;

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

        let scorerTouch = null;
        let assistTouch = null;
        for (let i = touchHistory.length - 1; i >= 0; i -= 1) {
          const touch = touchHistory[i];
          const age = goalFrame - touch.frameNo;
          if (age > ASSIST_TOUCH_WINDOW_FRAMES) break;

          if (!scorerTouch) {
            if (
              touch.teamId === teamId &&
              age <= GOAL_TOUCH_WINDOW_FRAMES
            ) {
              scorerTouch = touch;
              continue;
            }
            // If the last relevant touch belongs to the conceding team, avoid
            // inventing a scorer for a possible own-goal/deflection.
            if (touch.teamId !== teamId) break;
          } else {
            if (touch.teamId !== teamId) break;
            if (touch.playerId !== scorerTouch.playerId) {
              assistTouch = touch;
              break;
            }
          }
        }

        if (scorerTouch) {
          const scorer = ensurePlayer(scorerTouch.playerId);
          if (scorer) scorer.touchGoals += 1;
        }
        if (assistTouch) {
          const assister = ensurePlayer(assistTouch.playerId);
          if (assister) assister.touchAssists += 1;
        }

        lastKick = null;
        kickHistory = [];
        pendingKick = null;
        lastContact = null;
        lastTouch = null;
        touchHistory = [];
      },
      onGameStart: () => {
        gameStarts += 1;
        lastKick = null;
        kickHistory = [];
        pendingKick = null;
        lastContact = null;
        lastTouch = null;
        touchHistory = [];
      },
      onGameStop: () => {
        gameStops += 1;
        lastKick = null;
        kickHistory = [];
        pendingKick = null;
        lastContact = null;
        lastTouch = null;
        touchHistory = [];
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
      averagePressureDistance:
        p.pressureObservations > 0
          ? p.pressureDistanceSum / p.pressureObservations
          : null,
      underPressureTouchRate:
        p.pressureObservations > 0
          ? p.underPressureTouches / p.pressureObservations
          : null,
      touchRetentionRate:
        (p.teamTouchTransfersOut + p.turnovers) > 0
          ? p.teamTouchTransfersOut /
            (p.teamTouchTransfersOut + p.turnovers)
          : null,
      pressuredRetentionRate:
        p.pressuredTransitions > 0
          ? p.retainedUnderPressure / p.pressuredTransitions
          : null,
      averageTouchProgression:
        p.touchProgressionEvents > 0
          ? p.touchProgressionSum / p.touchProgressionEvents
          : null,
    }));

  const eventTypeCounts = Object.create(null);
  for (const event of replayData.events || []) {
    bump(eventTypeCounts, String(event.eventType));
  }

  const output = {
    schemaVersion: 4,
    featureVersion: "touch-chain-v1",
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
    featureSummary: {
      touches: playerRows.reduce((sum, p) => sum + p.touches, 0),
      ballCollisionEvents: playerRows.reduce(
        (sum, p) => sum + p.ballCollisionEvents,
        0,
      ),
      teamTouchTransfers: playerRows.reduce(
        (sum, p) => sum + p.teamTouchTransfersOut,
        0,
      ),
      turnovers: playerRows.reduce((sum, p) => sum + p.turnovers, 0),
      kickTransfersToTeammate: playerRows.reduce(
        (sum, p) => sum + p.kickTransfersToTeammate,
        0,
      ),
      kickTransfersToOpponent: playerRows.reduce(
        (sum, p) => sum + p.kickTransfersToOpponent,
        0,
      ),
      touchGoals: playerRows.reduce((sum, p) => sum + p.touchGoals, 0),
      touchAssists: playerRows.reduce((sum, p) => sum + p.touchAssists, 0),
      playersWithAuthHash: playerRows.reduce(
        (sum, p) => sum + (p.authHash ? 1 : 0),
        0,
      ),
    },
    players: playerRows,
  };

  process.stdout.write(JSON.stringify(output));
})().catch((error) => {
  console.error(error?.stack || String(error));
  process.exit(1);
});
