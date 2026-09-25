"use strict";

const { spawn } = require("child_process");
const readline = require("readline");

module.exports = function(API) {
  const {
    Plugin,
    AllowFlags,
    VariableType,
    Utils,
  } = API;

  Object.setPrototypeOf(this, Plugin.prototype);
  Plugin.call(this, "haxlabElite4v4", true, {
    version: "0.1.0",
    author: "HaxLab",
    description:
      "Role-conditioned elite imitation team (GK/DM/AM/ST) backed by the HaxLab temporal policy.",
    allowFlags: AllowFlags.CreateRoom,
  });

  const that = this;
  const roles = ["gk", "dm", "am", "st"];
  let processHandle = null;
  let lineReader = null;
  let requestCounter = 0;
  let tickCounter = 0;
  let bots = [];
  const pendingByRequest = new Map();

  this.defineVariable({
    name: "modelDir",
    type: VariableType.String,
    value:
      process.env.HAXLAB_ELITE_MODEL_DIR ||
      "/var/lib/haxlab/derived/training/elite-player-v01/model",
    description: "Directory containing model.npz and metrics.json.",
  });
  this.defineVariable({
    name: "pythonPath",
    type: VariableType.String,
    value:
      process.env.HAXLAB_ELITE_PYTHON ||
      "/opt/haxlab/.venv/bin/python",
    description: "Python executable used for the elite policy runtime.",
  });
  this.defineVariable({
    name: "sampleEveryTicks",
    type: VariableType.Integer,
    value: 6,
    range: { min: 1, max: 60, step: 1 },
    description: "How often the model receives a new state.",
  });
  this.defineVariable({
    name: "teamId",
    type: VariableType.Integer,
    value: 1,
    range: { min: 1, max: 2, step: 1 },
    description: "Team for the four AI players (1 red, 2 blue).",
  });
  this.defineVariable({
    name: "autoSpawn",
    type: VariableType.Boolean,
    value: process.env.HAXLAB_ELITE_AUTOSPAWN === "1",
    description: "Spawn GK/DM/AM/ST automatically when the plugin initializes.",
  });

  function num(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function discOf(player) {
    return player?.disc?.ext || player?.disc || null;
  }

  function entityVector(origin, other, sign) {
    const ownDisc = discOf(origin);
    const otherDisc = discOf(other);
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
    const ownDisc = discOf(origin);
    if (!ownDisc?.pos) return Array(limit * 5).fill(0);

    const ranked = candidates
      .filter((candidate) => discOf(candidate)?.pos)
      .map((candidate) => {
        const disc = discOf(candidate);
        const dx = num(disc.pos.x) - num(ownDisc.pos.x);
        const dy = num(disc.pos.y) - num(ownDisc.pos.y);
        return { candidate, distance2: dx * dx + dy * dy };
      })
      .sort((a, b) => a.distance2 - b.distance2)
      .slice(0, limit);

    const result = [];
    for (const item of ranked) {
      result.push(...entityVector(origin, item.candidate, sign));
    }
    while (result.length < limit * 5) result.push(0, 0, 0, 0, 0);
    return result;
  }

  function statePlayers(state) {
    if (Array.isArray(state?.players)) return state.players;
    if (Array.isArray(that.room?.state?.players)) return that.room.state.players;
    return [];
  }

  function scoreDiff(teamId) {
    const gameState = that.room?.gameStateExt || that.room?.gameState || {};
    const red = num(
      gameState.redScore ??
        gameState.scoreRed ??
        gameState.scores?.red ??
        gameState.scores?.[1],
    );
    const blue = num(
      gameState.blueScore ??
        gameState.scoreBlue ??
        gameState.scores?.blue ??
        gameState.scores?.[2],
    );
    return teamId === 1 ? red - blue : blue - red;
  }

  function featureObject(player, state, gameState) {
    const ownDisc = discOf(player);
    const ballDisc = gameState?.physicsState?.discs?.[0];
    if (!ownDisc?.pos || !ballDisc?.pos) return null;

    const teamId = Number(player.team?.id || 0);
    if (!(teamId === 1 || teamId === 2)) return null;
    const sign = teamId === 1 ? 1 : -1;
    const players = statePlayers(state);
    const teammates = players.filter(
      (candidate) =>
        candidate.id !== player.id &&
        candidate.team?.id === teamId &&
        discOf(candidate)?.pos,
    );
    const opponents = players.filter(
      (candidate) =>
        candidate.team?.id === 3 - teamId &&
        discOf(candidate)?.pos,
    );

    const values = [
      sign * num(ownDisc.pos.x),
      num(ownDisc.pos.y),
      sign * num(ownDisc.speed?.x),
      num(ownDisc.speed?.y),
      sign * (num(ballDisc.pos.x) - num(ownDisc.pos.x)),
      num(ballDisc.pos.y) - num(ownDisc.pos.y),
      sign * (num(ballDisc.speed?.x) - num(ownDisc.speed?.x)),
      num(ballDisc.speed?.y) - num(ownDisc.speed?.y),
      ...nearestVectors(player, teammates, sign, 3),
      ...nearestVectors(player, opponents, sign, 4),
      scoreDiff(teamId),
    ];

    const names = [
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

    return Object.fromEntries(names.map((name, index) => [name, values[index]]));
  }

  function sendRequest(bot, features) {
    if (!processHandle?.stdin?.writable || bot.inflight) return;
    const requestId = ++requestCounter;
    bot.inflight = true;
    pendingByRequest.set(requestId, bot);
    processHandle.stdin.write(
      JSON.stringify({
        command: "act",
        request_id: requestId,
        agent_id: String(bot.id),
        role: bot.role,
        features,
      }) + "\n",
    );
  }

  function applyAction(bot, response) {
    const state = that.room?.state;
    const player = state?.getPlayer?.(bot.id);
    if (!player) return;

    const dirX = Math.max(-1, Math.min(1, Number(response.dir_x) || 0));
    const dirY = Math.max(-1, Math.min(1, Number(response.dir_y) || 0));
    const kick = Boolean(response.kick);
    const keyState = Utils.keyState(dirX, dirY, kick);

    if (keyState !== bot.keyState || kick !== Boolean(player.isKicking)) {
      if (
        keyState === bot.keyState &&
        kick &&
        !player.isKicking
      ) {
        that.room.fakeSendPlayerInput(keyState & -17, bot.id);
      }
      that.room.fakeSendPlayerInput(keyState, bot.id);
      bot.keyState = keyState;
    }
    bot.lastAction = response;
  }

  function startPolicyProcess() {
    if (processHandle) return;
    processHandle = spawn(
      that.pythonPath,
      [
        "-m",
        "haxlab.learning.elite_runtime",
        "--model-dir",
        that.modelDir,
        "--stdio",
      ],
      {
        env: {
          ...process.env,
          PYTHONPATH:
            process.env.PYTHONPATH || "/opt/haxlab/src",
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );

    lineReader = readline.createInterface({ input: processHandle.stdout });
    lineReader.on("line", (line) => {
      let response;
      try {
        response = JSON.parse(line);
      } catch (_) {
        return;
      }
      const bot = pendingByRequest.get(Number(response.request_id));
      if (!bot) return;
      pendingByRequest.delete(Number(response.request_id));
      bot.inflight = false;
      if (response.ok) applyAction(bot, response);
      else {
        console.error("HaxLab elite policy error:", response.message || response);
      }
    });

    processHandle.stderr.on("data", (chunk) => {
      console.error("HaxLab elite runtime:", String(chunk).trim());
    });
    processHandle.on("exit", (code, signal) => {
      console.error(
        `HaxLab elite runtime exited code=${code} signal=${signal}`,
      );
      processHandle = null;
      pendingByRequest.clear();
      for (const bot of bots) bot.inflight = false;
    });
  }

  this.spawnEliteTeam = function(teamId = that.teamId) {
    if (bots.length) return bots.map((bot) => bot.id);
    startPolicyProcess();

    const firstId = 65000;
    bots = roles.map((role, index) => ({
      id: firstId - index,
      role,
      keyState: 0,
      inflight: false,
      lastAction: null,
    }));

    for (const bot of bots) {
      that.room.fakePlayerJoin(
        bot.id,
        `HaxLab-${bot.role.toUpperCase()}`,
        "xx",
        "AI",
        `haxlab-${bot.id}`,
        `haxlab-elite-${bot.role}`,
      );
      that.room.fakeSetPlayerTeam(bot.id, Number(teamId), 0);
    }
    return bots.map((bot) => bot.id);
  };

  this.removeEliteTeam = function() {
    for (const bot of bots) {
      try {
        that.room.fakePlayerLeave(bot.id);
      } catch (_) {}
    }
    bots = [];
    pendingByRequest.clear();
  };

  this.initialize = function() {
    startPolicyProcess();
    if (that.autoSpawn) that.spawnEliteTeam(that.teamId);
  };

  this.finalize = function() {
    that.removeEliteTeam();
    try {
      lineReader?.close();
    } catch (_) {}
    try {
      processHandle?.kill();
    } catch (_) {}
    processHandle = null;
    lineReader = null;
  };

  this.onGameStart = function() {
    tickCounter = 0;
    for (const bot of bots) {
      bot.inflight = false;
      bot.keyState = 0;
      try {
        processHandle?.stdin?.write(
          JSON.stringify({
            command: "reset",
            request_id: ++requestCounter,
            agent_id: String(bot.id),
          }) + "\n",
        );
      } catch (_) {}
    }
  };

  this.onGameTick = function() {
    if (!bots.length || !processHandle) return;
    tickCounter += 1;
    if (tickCounter % Math.max(1, Number(that.sampleEveryTicks) || 6) !== 0) {
      return;
    }

    that.room.extrapolate();
    const state = that.room.state;
    const gameState = that.room.gameStateExt || that.room.gameState;
    if (!state || !gameState?.physicsState?.discs?.[0]?.pos) return;

    for (const bot of bots) {
      const player = state.getPlayer?.(bot.id);
      if (!player || !discOf(player)?.pos) continue;
      const features = featureObject(player, state, gameState);
      if (features) sendRequest(bot, features);
    }
  };
};
