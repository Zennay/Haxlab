"use strict";

const path = require("path");
const { ElitePolicyRuntime } = require("./elite_policy_runtime");
const {
  discOf,
  buildFeatureObject,
  canonicalActionToWorld,
} = require("./elite_features");
const {
  kickoffAction,
  enforceKickRange,
  recoveryAction,
  shouldRecoverFromStall,
} = require("./elite_tactics");

module.exports = function(API) {
  const {
    Plugin,
    AllowFlags,
    VariableType,
    Utils,
  } = API;

  Object.setPrototypeOf(this, Plugin.prototype);
  Plugin.call(this, "haxlabElite4v4", true, {
    version: "0.2.0",
    author: "HaxLab",
    description:
      "Role-conditioned elite imitation team (GK/DM/AM/ST) with in-process Node inference.",
    allowFlags: AllowFlags.CreateRoom,
  });

  const that = this;
  const roles = ["gk", "dm", "am", "st"];
  let tickCounter = 0;
  let bots = [];
  let policy = null;
  let runtimeErrorCount = 0;

  this.defineVariable({
    name: "modelDir",
    type: VariableType.String,
    value:
      process.env.HAXLAB_ELITE_MODEL_DIR ||
      "/var/lib/haxlab/derived/training/elite-player-v01/model",
    description: "Directory containing runtime-model.json and metrics.json.",
  });
  this.defineVariable({
    name: "sampleEveryTicks",
    type: VariableType.Integer,
    value: 6,
    range: { min: 1, max: 60, step: 1 },
    description: "How often the policy receives a new state.",
  });
  this.defineVariable({
    name: "teamId",
    type: VariableType.Integer,
    value: 1,
    range: { min: 1, max: 2, step: 1 },
    description: "Team for the four AI players (1 red, 2 blue).",
  });
  this.defineVariable({
    name: "enableRecovery",
    type: VariableType.Boolean,
    value: process.env.HAXLAB_ELITE_RECOVERY === "1",
    description:
      "Enable the closed-loop anti-stall recovery guard. Off by default until benchmark promotion.",
  });
  this.defineVariable({
    name: "recoveryMinDistance",
    type: VariableType.Integer,
    value: 240,
    range: { min: 60, max: 800, step: 10 },
    description: "Minimum ball distance before stalled movement can trigger recovery.",
  });
  this.defineVariable({
    name: "recoveryStallDecisions",
    type: VariableType.Integer,
    value: 6,
    range: { min: 2, max: 20, step: 1 },
    description: "Consecutive stationary decisions required before recovery.",
  });
  this.defineVariable({
    name: "recoveryTicks",
    type: VariableType.Integer,
    value: 2,
    range: { min: 1, max: 12, step: 1 },
    description: "Policy decision steps to keep a recovery trajectory active.",
  });

  this.defineVariable({
    name: "autoSpawn",
    type: VariableType.Boolean,
    value: process.env.HAXLAB_ELITE_AUTOSPAWN === "1",
    description: "Spawn GK/DM/AM/ST automatically when the plugin initializes.",
  });

  function teamIdForPlayer(player) {
    return Number(player?.team?.id || player?.teamId || 0);
  }

  function ensurePolicy() {
    if (policy) return policy;
    policy = ElitePolicyRuntime.fromFile(
      path.join(String(that.modelDir), "runtime-model.json"),
    );
    return policy;
  }

  function applyAction(bot, action) {
    const state = that.room?.state;
    const player = state?.getPlayer?.(bot.id);
    if (!player) return;

    const teamId = Number(player.team?.id || 0);
    const worldAction = enforceKickRange(
      canonicalActionToWorld(action, teamId),
      player,
      that.room?.gameStateExt || that.room?.gameState,
    );
    const kick = worldAction.kick;
    const keyState = Utils.keyState(
      worldAction.dirX,
      worldAction.dirY,
      worldAction.kick,
    );

    if (keyState !== bot.keyState || kick !== Boolean(player.isKicking)) {
      if (keyState === bot.keyState && kick && !player.isKicking) {
        that.room.fakeSendPlayerInput(keyState & -17, bot.id);
      }
      that.room.fakeSendPlayerInput(keyState, bot.id);
      bot.keyState = keyState;
    }
    bot.lastAction = action;
  }

  this.spawnEliteTeam = function(teamId = that.teamId) {
    if (bots.length) return bots.map((bot) => bot.id);
    ensurePolicy();

    const firstId = 65000;
    bots = roles.map((role, index) => ({
      id: firstId - index,
      role,
      keyState: 0,
      lastAction: null,
      stallStreak: 0,
      recoveryTicks: 0,
      recoveryOverrides: 0,
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
    policy?.reset();
  };

  this.initialize = function() {
    ensurePolicy();
    if (that.autoSpawn) that.spawnEliteTeam(that.teamId);
  };

  this.finalize = function() {
    that.removeEliteTeam();
    policy = null;
  };

  this.onGameStart = function() {
    tickCounter = 0;
    runtimeErrorCount = 0;
    for (const bot of bots) {
      bot.keyState = 0;
      bot.stallStreak = 0;
      bot.recoveryTicks = 0;
      bot.recoveryOverrides = 0;
      policy?.reset(String(bot.id));
    }
  };

  this.onGameTick = function() {
    if (!bots.length) return;
    tickCounter += 1;
    if (
      tickCounter % Math.max(1, Number(that.sampleEveryTicks) || 6) !== 0
    ) {
      return;
    }

    that.room.extrapolate();
    const state = that.room.state;
    const gameState = that.room.gameStateExt || that.room.gameState;
    if (!state || !gameState?.physicsState?.discs?.[0]?.pos) return;

    for (const bot of bots) {
      const player = state.getPlayer?.(bot.id);
      if (!player || !discOf(player)?.pos) continue;
      const features = buildFeatureObject(player, state, gameState);
      if (!features) continue;

      try {
        const tactical = kickoffAction(bot.role, player, gameState);
        if (tactical) {
          const canonicalTactical = {
            dir_x: teamIdForPlayer(player) === 2 ? -tactical.dirX : tactical.dirX,
            dir_y: tactical.dirY,
            kick: tactical.kick,
          };
          applyAction(bot, canonicalTactical);
          continue;
        }

        const action = ensurePolicy().act({
          agent_id: String(bot.id),
          role: bot.role,
          features,
        });

        const ballDistance = Math.hypot(
          Number(features.ball_dx || 0),
          Number(features.ball_dy || 0),
        );
        const stationary =
          Number(action.dir_x || 0) === 0 &&
          Number(action.dir_y || 0) === 0;
        bot.stallStreak =
          stationary && ballDistance >= 120
            ? bot.stallStreak + 1
            : 0;

        if (
          that.enableRecovery &&
          bot.recoveryTicks <= 0 &&
          shouldRecoverFromStall(
            { dirX: action.dir_x, dirY: action.dir_y },
            ballDistance,
            bot.stallStreak,
            {
              minimumBallDistance: Number(that.recoveryMinDistance) || 240,
              minimumStallDecisions:
                Number(that.recoveryStallDecisions) || 6,
            },
          )
        ) {
          bot.recoveryTicks = Number(that.recoveryTicks) || 2;
          bot.recoveryOverrides += 1;
          bot.stallStreak = 0;
        }

        if (that.enableRecovery && bot.recoveryTicks > 0) {
          const teamId = teamIdForPlayer(player);
          const recovery = recoveryAction(
            bot.role,
            player,
            gameState,
            teamId,
          );
          if (recovery) {
            bot.recoveryTicks -= 1;
            applyAction(bot, {
              dir_x: teamId === 2 ? -recovery.dirX : recovery.dirX,
              dir_y: recovery.dirY,
              kick: recovery.kick,
              source: recovery.source,
            });
            continue;
          }
          bot.recoveryTicks = 0;
        }

        applyAction(bot, action);
      } catch (error) {
        runtimeErrorCount += 1;
        if (runtimeErrorCount <= 5) {
          console.error(
            "HaxLab elite in-process runtime error:",
            error?.stack || String(error),
          );
        }
      }
    }
  };
};
