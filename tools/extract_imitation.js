#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const zlib = require("zlib");
const initAPI = require("node-haxball");

const API = initAPI();
const { Replay, Utils } = API;

function usage() {
  console.error(
    "Usage: node tools/extract_imitation.js <replay.hbr2> <output.jsonl.gz> " +
      "<selectedPlayerMapJson> [sampleEveryTicks]",
  );
  process.exit(2);
}

const replayPath = process.argv[2];
const outputPath = process.argv[3];
const selectedPlayerMapJson = process.argv[4];
const sampleEvery = Math.max(
  1,
  Number.parseInt(process.argv[5] || "6", 10) || 6,
);

if (!replayPath || !outputPath || !selectedPlayerMapJson) usage();
if (os.endianness() !== "LE") {
  throw new Error("haxlab imitation shards currently require little-endian host");
}

const selectedByReplayId = new Map(
  Object.entries(JSON.parse(selectedPlayerMapJson)).map(
    ([playerId, identity]) => [Number(playerId), String(identity)],
  ),
);
const selectedIdentities = Array.from(
  new Set(selectedByReplayId.values()),
).sort();
const selectedIndex = new Map(
  selectedIdentities.map((identity, index) => [identity, index]),
);

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function entityVector(origin, other, sign) {
  const ownDisc = origin?.disc;
  const otherDisc = other?.disc;
  if (!ownDisc?.pos || !otherDisc?.pos) return [0, 0, 0, 0, 0];

  return [
    sign * (num(otherDisc.pos.x) - num(ownDisc.pos.x)),
    num(otherDisc.pos.y) - num(ownDisc.pos.y),
    sign * (num(otherDisc.speed?.x) - num(ownDisc.speed?.x)),
    num(otherDisc.speed?.y) - num(ownDisc.speed?.y),
    1,
  ];
}

function nearestVectors(origin, candidates, sign, limit) {
  const ownPos = origin?.disc?.pos;
  if (!ownPos) {
    return Array.from({ length: limit * 5 }, () => 0);
  }

  const ranked = candidates
    .filter((candidate) => candidate?.disc?.pos)
    .map((candidate) => {
      const dx = num(candidate.disc.pos.x) - num(ownPos.x);
      const dy = num(candidate.disc.pos.y) - num(ownPos.y);
      return {
        candidate,
        distance2: dx * dx + dy * dy,
      };
    })
    .sort((a, b) => a.distance2 - b.distance2)
    .slice(0, limit);

  const result = [];
  for (const item of ranked) {
    result.push(...entityVector(origin, item.candidate, sign));
  }
  while (result.length < limit * 5) {
    result.push(0, 0, 0, 0, 0);
  }
  return result;
}

const data = Uint8Array.from(fs.readFileSync(replayPath));
const replayData = Replay.readAll(data);
const currentInputs = new Map();

let reader = null;
let settled = false;
let timeout = null;
let sampleCount = 0;
let selectedStateSamples = 0;
let skippedUnknownInput = 0;
let framesAdvanced = 0;
let selectedPlayersSeen = new Set();

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
const temporaryPath = `${outputPath}.tmp-${process.pid}`;
const output = fs.createWriteStream(temporaryPath);
const gzip = zlib.createGzip({ level: 6 });
gzip.pipe(output);

const columns = [
  "frame",
  "player_index",
  "team_id",
  "own_x",
  "own_y",
  "own_vx",
  "own_vy",
  "ball_dx",
  "ball_dy",
  "ball_dvx",
  "ball_dvy",
  "tm1_dx",
  "tm1_dy",
  "tm1_dvx",
  "tm1_dvy",
  "tm1_present",
  "tm2_dx",
  "tm2_dy",
  "tm2_dvx",
  "tm2_dvy",
  "tm2_present",
  "op1_dx",
  "op1_dy",
  "op1_dvx",
  "op1_dvy",
  "op1_present",
  "op2_dx",
  "op2_dy",
  "op2_dvx",
  "op2_dvy",
  "op2_present",
  "op3_dx",
  "op3_dy",
  "op3_dvx",
  "op3_dvy",
  "op3_present",
  "dir_x",
  "dir_y",
  "kick",
];

function writeSample(player, statePlayers, ballDisc) {
  const replayPlayerId = Number(player.id);
  const identity = selectedByReplayId.get(replayPlayerId);
  if (!identity) return;
  if (!(player.team?.id === 1 || player.team?.id === 2)) return;
  if (!player.disc?.pos || !ballDisc?.pos) return;

  selectedPlayersSeen.add(replayPlayerId);
  selectedStateSamples += 1;

  const playerInput =
    currentInputs.has(player.id)
      ? currentInputs.get(player.id)
      : Number.isInteger(player.input)
        ? player.input
        : null;

  if (playerInput == null) {
    skippedUnknownInput += 1;
    return;
  }

  let action;
  try {
    action = Utils.reverseKeyState(playerInput);
  } catch (_) {
    skippedUnknownInput += 1;
    return;
  }

  const teamId = Number(player.team.id);
  const sign = teamId === 1 ? 1 : -1;
  const ownDisc = player.disc;
  const teammates = statePlayers.filter(
    (candidate) =>
      candidate.id !== player.id &&
      candidate.team?.id === teamId &&
      candidate.disc?.pos,
  );
  const opponents = statePlayers.filter(
    (candidate) =>
      candidate.team?.id === 3 - teamId &&
      candidate.disc?.pos,
  );

  const sample = [
    Number(reader.getCurrentFrameNo()),
    selectedIndex.get(identity),
    teamId,
    sign * num(ownDisc.pos.x),
    num(ownDisc.pos.y),
    sign * num(ownDisc.speed?.x),
    num(ownDisc.speed?.y),
    sign * (num(ballDisc.pos.x) - num(ownDisc.pos.x)),
    num(ballDisc.pos.y) - num(ownDisc.pos.y),
    sign * (num(ballDisc.speed?.x) - num(ownDisc.speed?.x)),
    num(ballDisc.speed?.y) - num(ownDisc.speed?.y),
    ...nearestVectors(player, teammates, sign, 2),
    ...nearestVectors(player, opponents, sign, 3),
    sign * num(action?.dirX),
    num(action?.dirY),
    action?.kick ? 1 : 0,
  ];

  const values = new Float32Array(sample);
  gzip.write(Buffer.from(values.buffer, values.byteOffset, values.byteLength));
  sampleCount += 1;
}

function sampleState() {
  const gameState = reader?.gameState;
  const statePlayers = reader?.state?.players || [];
  if (!gameState?.physicsState?.discs?.length) return;

  const frameNo = Number(reader.getCurrentFrameNo());
  if (frameNo % sampleEvery !== 0) return;

  const ballDisc = gameState.physicsState.discs[0];
  if (!ballDisc?.pos) return;

  for (const player of statePlayers) {
    writeSample(player, statePlayers, ballDisc);
  }
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

  try {
    reader?.destroy?.();
  } catch (_) {}

  if (error) reject(error);
  else resolve();
}

function runReplay() {
  return new Promise((resolve, reject) => {
    const callbacks = {
      onGameTick: () => {
        sampleState();
      },
      onPlayerInputChange: (id, input) => {
        currentInputs.set(Number(id), Number(input));
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
      () => finish(resolve, reject, new Error("extractor_timeout")),
      120000,
    );
    reader.setSpeed(100000);
  });
}

function closeOutput() {
  return new Promise((resolve, reject) => {
    output.once("close", resolve);
    output.once("error", reject);
    gzip.once("error", reject);
    gzip.end();
  });
}

(async () => {
  try {
    await runReplay();

    if (
      replayData.totalFrames > 0 &&
      framesAdvanced < Math.max(0, replayData.totalFrames - 1)
    ) {
      throw new Error(
        `incomplete_reconstruction:${framesAdvanced}/${replayData.totalFrames}`,
      );
    }

    await closeOutput();
    fs.renameSync(temporaryPath, outputPath);

    process.stdout.write(
      JSON.stringify({
        schema: "haxlab-imitation-extract-summary-v2",
        shardSchema: "haxlab-imitation-shard-v2",
        format: "float32-le-gzip",
        dtype: "float32-le",
        rowWidth: columns.length,
        columns,
        canonicalAttackDirection: "+x",
        selectedPlayers: Object.fromEntries(
          Array.from(selectedIndex.entries()).map(([identity, index]) => [
            String(index),
            identity,
          ]),
        ),
        selectedReplayPlayers: Object.fromEntries(
          Array.from(selectedByReplayId.entries()).map(([playerId, identity]) => [
            String(playerId),
            identity,
          ]),
        ),
        sourceFile: path.basename(replayPath),
        outputPath,
        totalFrames: replayData.totalFrames,
        framesAdvanced,
        sampleEveryTicks: sampleEvery,
        samples: sampleCount,
        selectedStateSamples,
        skippedUnknownInput,
        selectedPlayersRequested: selectedByReplayId.size,
        selectedPlayersSeen: selectedPlayersSeen.size,
        compressedBytes: fs.statSync(outputPath).size,
      }),
    );
  } catch (error) {
    try {
      gzip.destroy();
      output.destroy();
    } catch (_) {}
    try {
      fs.unlinkSync(temporaryPath);
    } catch (_) {}
    console.error(error?.stack || String(error));
    process.exit(1);
  }
})();
