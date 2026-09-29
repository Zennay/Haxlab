#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const readline = require("readline");
const initAPI = require("node-haxball");
const API = initAPI();
const { Room, Utils } = API;

const roomId = String(process.env.HAXLAB_ROOM_ID || "").trim();
const roomPassword = String(process.env.HAXLAB_ROOM_PASSWORD || "").trim() || null;
const playerName = String(process.env.HAXLAB_PLAYER_NAME || "HaxLab AI");
const playerAvatar = String(process.env.HAXLAB_PLAYER_AVATAR || "AI").slice(0, 2);
const role = String(process.env.HAXLAB_LIVE_ROLE || "st");
const championVersion = String(process.env.HAXLAB_CHAMPION_VERSION || "").trim();
const inferEveryTicks = Math.max(1, Number.parseInt(process.env.HAXLAB_INFER_EVERY_TICKS || "2", 10) || 2);

if (!/^[A-Za-z0-9_-]{4,80}$/.test(roomId)) {
  console.error("HAXLAB_ROOM_ID is missing or invalid.");
  process.exit(2);
}

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function discOf(player) {
  return player?.disc?.ext || player?.disc || null;
}

function entityVector(origin, other, sign) {
  const own = discOf(origin);
  const target = discOf(other);
  if (!own?.pos || !target?.pos) return [0, 0, 0, 0, 0];
  return [
    sign * (num(target.pos.x) - num(own.pos.x)),
    num(target.pos.y) - num(own.pos.y),
    sign * (num(target.speed?.x) - num(own.speed?.x)),
    num(target.speed?.y) - num(own.speed?.y),
    1,
  ];
}

function nearestVectors(origin, candidates, sign, limit) {
  const own = discOf(origin);
  if (!own?.pos) return Array.from({ length: limit * 5 }, () => 0);
  const ranked = candidates
    .filter((candidate) => discOf(candidate)?.pos)
    .map((candidate) => {
      const d = discOf(candidate);
      const dx = num(d.pos.x) - num(own.pos.x);
      const dy = num(d.pos.y) - num(own.pos.y);
      return { candidate, distance2: dx * dx + dy * dy };
    })
    .sort((a, b) => a.distance2 - b.distance2)
    .slice(0, limit);
  const result = [];
  for (const item of ranked) result.push(...entityVector(origin, item.candidate, sign));
  while (result.length < limit * 5) result.push(0, 0, 0, 0, 0);
  return result;
}

const FEATURE_NAMES = [
  "own_x", "own_y", "own_vx", "own_vy",
  "ball_dx", "ball_dy", "ball_dvx", "ball_dvy",
  "tm1_dx", "tm1_dy", "tm1_dvx", "tm1_dvy", "tm1_present",
  "tm2_dx", "tm2_dy", "tm2_dvx", "tm2_dvy", "tm2_present",
  "tm3_dx", "tm3_dy", "tm3_dvx", "tm3_dvy", "tm3_present",
  "op1_dx", "op1_dy", "op1_dvx", "op1_dvy", "op1_present",
  "op2_dx", "op2_dy", "op2_dvx", "op2_dvy", "op2_present",
  "op3_dx", "op3_dy", "op3_dvx", "op3_dvy", "op3_present",
  "op4_dx", "op4_dy", "op4_dvx", "op4_dvy", "op4_present",
  "score_diff",
];

function buildFeatures(room) {
  room.extrapolate();
  const player = room.currentPlayer;
  const own = discOf(player);
  const teamId = Number(player?.team?.id || 0);
  if (!(teamId === 1 || teamId === 2) || !own?.pos) return null;

  const gameState = room.gameStateExt || room.gameState;
  const ball = gameState?.physicsState?.discs?.[0];
  if (!ball?.pos) return null;

  const sign = teamId === 1 ? 1 : -1;
  const players = room.state?.players || room.players || [];
  const teammates = players.filter((candidate) =>
    candidate?.id !== player?.id &&
    Number(candidate?.team?.id || 0) === teamId &&
    discOf(candidate)?.pos
  );
  const opponents = players.filter((candidate) =>
    Number(candidate?.team?.id || 0) === 3 - teamId &&
    discOf(candidate)?.pos
  );

  const values = [
    sign * num(own.pos.x),
    num(own.pos.y),
    sign * num(own.speed?.x),
    num(own.speed?.y),
    sign * (num(ball.pos.x) - num(own.pos.x)),
    num(ball.pos.y) - num(own.pos.y),
    sign * (num(ball.speed?.x) - num(own.speed?.x)),
    num(ball.speed?.y) - num(own.speed?.y),
    ...nearestVectors(player, teammates, sign, 3),
    ...nearestVectors(player, opponents, sign, 4),
    teamId === 1
      ? num(gameState?.redScore) - num(gameState?.blueScore)
      : num(gameState?.blueScore) - num(gameState?.redScore),
  ];

  return {
    features: Object.fromEntries(FEATURE_NAMES.map((name, index) => [name, values[index]])),
    sign,
    teamId,
  };
}

const pythonArgs = ["-u", "-m", "haxlab.live.inference", "--role", role];
if (championVersion) pythonArgs.push("--version", championVersion);

const inference = spawn(
  process.env.HAXLAB_PYTHON || "/opt/haxlab/.venv/bin/python",
  pythonArgs,
  {
    cwd: process.env.HAXLAB_APP_DIR || "/opt/haxlab",
    stdio: ["pipe", "pipe", "pipe"],
    env: process.env,
  }
);

let room = null;
let inferenceReady = false;
let inferencePending = false;
let tick = 0;
let lastTeamId = 0;
let lastAction = 0;
let lastCanonical = null;

inference.stderr.on("data", (chunk) => {
  process.stderr.write("[inference] " + chunk.toString());
});

inference.on("exit", (code, signal) => {
  console.error("Inference process exited: code=" + code + " signal=" + (signal || ""));
  try { room?.setKeyState?.(0); } catch (_) {}
  if (code !== 0) process.exit(code || 1);
});

const output = readline.createInterface({ input: inference.stdout });
output.on("line", (line) => {
  let message;
  try {
    message = JSON.parse(line);
  } catch (_) {
    console.error("Invalid inference JSON:", line);
    inferencePending = false;
    return;
  }

  if (message.type === "ready") {
    inferenceReady = true;
    console.log(
      "HAXLAB_MODEL_READY version=" + message.version +
      " role=" + message.role +
      " window=" + message.window +
      " frame_dim=" + message.frame_dim
    );
    return;
  }
  if (message.type === "error") {
    inferencePending = false;
    console.error("Inference error:", message.error);
    return;
  }
  if (message.type !== "action") return;

  inferencePending = false;
  if (!room || !lastCanonical) return;

  const dirX = lastCanonical.sign * num(message.dir_x);
  const dirY = num(message.dir_y);
  const kick = Boolean(message.kick);
  const keyState = Utils.keyState(dirX, dirY, kick);

  try {
    const current = room.currentPlayer;
    if (!current || Number(current.team?.id || 0) !== lastCanonical.teamId) return;
    if (keyState !== lastAction || kick !== Boolean(current.isKicking)) {
      room.setKeyState(keyState);
      lastAction = keyState;
    }
  } catch (error) {
    console.error("Failed to apply key state:", error?.stack || String(error));
  }
});

function resetInference() {
  if (!inference.stdin.destroyed) {
    inference.stdin.write(JSON.stringify({ op: "reset" }) + "\n");
  }
}

function roomCallbacks(joinedRoom) {
  room = joinedRoom;
  console.log("HAXLAB_ROOM_JOINED id=" + roomId + " name=" + playerName);

  room.onGameTick = () => {
    tick += 1;
    const currentTeam = Number(room.currentPlayer?.team?.id || 0);
    if (currentTeam !== lastTeamId) {
      lastTeamId = currentTeam;
      lastAction = 0;
      inferencePending = false;
      resetInference();
      console.log("HAXLAB_TEAM team=" + currentTeam);
    }

    if (!inferenceReady || inferencePending || tick % inferEveryTicks !== 0) return;

    const canonical = buildFeatures(room);
    if (!canonical) {
      if (lastAction !== 0) {
        try { room.setKeyState(0); } catch (_) {}
        lastAction = 0;
      }
      return;
    }

    lastCanonical = canonical;
    inferencePending = true;
    inference.stdin.write(
      JSON.stringify({ features: canonical.features }) + "\n",
      (error) => {
        if (error) {
          inferencePending = false;
          console.error("Failed to send inference state:", error.message);
        }
      }
    );
  };

  room.onAfterGameStart = () => {
    inferencePending = false;
    resetInference();
  };
}

Utils.generateAuth()
  .then(([authKey, authObj]) => {
    Room.join(
      { id: roomId, password: roomPassword, authObj },
      {
        storage: {
          player_name: playerName,
          avatar: playerAvatar,
          player_auth_key: authKey,
        },
        config: null,
        renderer: null,
        plugins: [],
        onOpen: roomCallbacks,
        onClose: (message) => {
          console.log("HAXLAB_ROOM_CLOSED " + (message?.toString?.() || String(message || "")));
          try { inference.kill("SIGTERM"); } catch (_) {}
          process.exit(0);
        },
      }
    );
  })
  .catch((error) => {
    console.error("Failed to generate auth or join room:", error?.stack || String(error));
    process.exit(1);
  });

function shutdown(signal) {
  console.log("HAXLAB_SHUTDOWN signal=" + signal);
  try { room?.setKeyState?.(0); } catch (_) {}
  try { inference.kill("SIGTERM"); } catch (_) {}
  process.exit(0);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
