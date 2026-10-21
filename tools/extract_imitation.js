#!/usr/bin/env node
"use strict";

const fs = require("fs");
const crypto = require("crypto");
const os = require("os");
const path = require("path");
const zlib = require("zlib");
const initAPI = require("node-haxball");
const { parseSelectedPlayerMapJson, parseSampleEveryTicks } = require("./extract_imitation_args");

const API = initAPI();
const { Replay, Utils } = API;

function usage() {
  console.error(
    "Usage: node tools/extract_imitation.js <replay.hbr2> <output.jsonl.gz> " +
      "<selectedPlayerMapJson> [sampleEveryTicks] [receiptJsonPath]",
  );
  process.exit(2);
}

const replayPath = process.argv[2];
const outputPath = process.argv[3];
const selectedPlayerMapJson = process.argv[4];
const sampleEvery = parseSampleEveryTicks(process.argv[5]);
const receiptPath = process.argv[6] || null;
if (process.argv.length > 7 || (process.argv[6] !== undefined && !receiptPath)) usage();
if (receiptPath && [
  path.resolve(outputPath || "."),
  path.resolve(replayPath || "."),
].includes(path.resolve(receiptPath))) {
  throw new Error("receipt_file_must_be_distinct_from_replay_and_shard");
}

if (!replayPath || !outputPath || !selectedPlayerMapJson) usage();
if (os.endianness() !== "LE") {
  throw new Error("haxlab imitation shards currently require little-endian host");
}

const selectedByReplayId = parseSelectedPlayerMapJson(selectedPlayerMapJson);

// A repeat request can take minutes of replay reconstruction before a
// create-only final hardlink discovers that the shard or receipt already
// exists. Reject occupied names before reading/decoding the replay.
// lstatSync also rejects dangling symlinks (existsSync would miss those).
// The final hardlink remains the atomic authority if a race occurs later.
function requireAvailableDestination(destination, role) {
  try {
    fs.lstatSync(destination);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  throw new Error(role + "_destination_already_exists");
}
requireAvailableDestination(outputPath, "shard");
if (receiptPath) requireAvailableDestination(receiptPath, "receipt");

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
// Bind the machine-readable extraction receipt to the exact replay bytes,
// caller-supplied player-map JSON and final compressed artifact. Filenames
// and apparently equivalent normalized player maps are not provenance.
const sourceReplaySha256 = crypto.createHash("sha256").update(data).digest("hex");
const selectedPlayerMapSha256 = crypto.createHash("sha256")
  .update(selectedPlayerMapJson, "utf8").digest("hex");
const compressedDigest = crypto.createHash("sha256");
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
const temporaryPath = `${outputPath}.tmp-${process.pid}-${crypto.randomBytes(8).toString("hex")}`;
const receiptTemporaryPath = receiptPath
  ? `${receiptPath}.tmp-${process.pid}-${crypto.randomBytes(8).toString("hex")}`
  : null;
// Exclusive private creation: never follow/truncate an existing temporary
// path (including a symlink) when replay extraction is retried or overlaps.
const output = fs.createWriteStream(temporaryPath, { flags: "wx", mode: 0o600 });
const gzip = zlib.createGzip({ level: 6 });
// Errors may happen during the replay (well before closeOutput installs its
// one-shot listeners). Keep the failure observed rather than allowing an
// unhandled stream error or publishing an incomplete shard.
let writeFailure = null;
let abortReplayOnWriteFailure = null;
function recordWriteFailure(error) {
  if (!writeFailure) writeFailure = error;
  // Do not burn through the remaining replay after the shard sink has failed.
  // The replay promise owns its own settlement and destroys the reader.
  abortReplayOnWriteFailure?.(writeFailure);
}
output.on("error", recordWriteFailure);
gzip.on("error", recordWriteFailure);
// Hash exactly the compressed byte stream that is piped to the output.
// This does not materialize a second copy of potentially large shards.
gzip.on("data", (chunk) => { compressedDigest.update(chunk); });
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
    abortReplayOnWriteFailure = (error) => finish(resolve, reject, error);
    if (writeFailure) {
      abortReplayOnWriteFailure(writeFailure);
      return;
    }
    const callbacks = {
      onGameTick: () => {
        if (settled) return;
        // Replay callbacks execute asynchronously after the promise executor
        // returns. Catch state/stream exceptions here so they reject the
        // owning extraction instead of escaping as uncaught process errors.
        try {
          sampleState();
        } catch (error) {
          finish(resolve, reject, error);
        }
      },
      onPlayerInputChange: (id, input) => {
        if (!settled) currentInputs.set(Number(id), Number(input));
      },
    };

    const fastRAF = (callback) => setImmediate(() => callback(Date.now()));
    const fastCancelRAF = (handle) => clearImmediate(handle);

    try {
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
    } catch (error) {
      finish(resolve, reject, error);
    }
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

    // Never present an empty compressed shard as successfully extracted
    // training data. A selected map may refer to players never sampled, or
    // every observed selected state may lack a recoverable action label.
    // In both cases no usable imitation row exists; keep the final shard
    // unpublished and let the existing failure cleanup remove the temp file.
    if (sampleCount === 0) {
      throw new Error(
        selectedStateSamples === 0
          ? "no_selected_players_in_sampled_states"
          : "no_selected_samples_with_known_input",
      );
    }

    if (writeFailure) throw writeFailure;
    await closeOutput();
    if (writeFailure) throw writeFailure;
    const shardCompressedSha256 = compressedDigest.digest("hex");
    // Derive the receipt from the exact sealed TEMPORARY shard. Only after
    // this do we publish its final name, never overwriting an existing shard.
    const summary = {
      schema: "haxlab-imitation-extract-summary-v2",
      shardSchema: "haxlab-imitation-shard-v2",
      format: "float32-le-gzip",
      dtype: "float32-le",
      rowWidth: columns.length,
      columns,
      canonicalAttackDirection: "+x",
      selectedPlayers: Object.fromEntries(
        Array.from(selectedIndex.entries()).map(([identity, index]) => [
          String(index), identity,
        ]),
      ),
      selectedReplayPlayers: Object.fromEntries(
        Array.from(selectedByReplayId.entries()).map(([playerId, identity]) => [
          String(playerId), identity,
        ]),
      ),
      sourceFile: path.basename(replayPath),
      sourceReplaySha256,
      selectedPlayerMapSha256,
      shardCompressedSha256,
      outputPath,
      totalFrames: replayData.totalFrames,
      framesAdvanced,
      sampleEveryTicks: sampleEvery,
      samples: sampleCount,
      selectedStateSamples,
      skippedUnknownInput,
      selectedPlayersRequested: selectedByReplayId.size,
      selectedPlayersSeen: selectedPlayersSeen.size,
      compressedBytes: fs.statSync(temporaryPath).size,
    };
    // Optional machine-readable provenance survives when the caller does not
    // retain stdout. Its temporary file is private and exclusively created.
    // Same-directory hard links are create-only, never rename-overwrite.
    if (receiptTemporaryPath) {
      fs.writeFileSync(receiptTemporaryPath,
        JSON.stringify(summary, null, 2) + "\n",
        { flag: "wx", mode: 0o600 });
    }
    fs.linkSync(temporaryPath, outputPath);
    if (receiptTemporaryPath) {
      // A failure to publish the secondary receipt leaves an explicitly
      // INCOMPLETE deliverable (no successful stdout). Never silently delete
      // a committed shard; its immutable bytes may already be consumed.
      fs.linkSync(receiptTemporaryPath, receiptPath);
      fs.unlinkSync(receiptTemporaryPath);
    }
    fs.unlinkSync(temporaryPath);
    process.stdout.write(JSON.stringify(summary));
  } catch (error) {
    // fs.createWriteStream may still be opening asynchronously when replay
    // validation fails. Deleting the temp path *before* "close" races the
    // pending open, potentially leaving orphaned private partial shards.
    // Wait for stream teardown, then unlink our private temporary artifacts.
    const closed = output.closed
      ? Promise.resolve()
      : new Promise((resolve) => output.once("close", resolve));
    gzip.destroy();
    output.destroy();
    await closed;
    try { fs.unlinkSync(temporaryPath); } catch (_) {}
    if (receiptTemporaryPath) {
      try { fs.unlinkSync(receiptTemporaryPath); } catch (_) {}
    }
    console.error(error?.stack || String(error));
    process.exitCode = 1;
  }
})();
