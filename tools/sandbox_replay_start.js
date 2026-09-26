"use strict";

const initAPI = require("node-haxball");
const { ROLE_STARTS } = require("./sandbox_neutral_start");

const API = initAPI();
const { Utils } = API;

function setPlayerDisc(room, playerId, properties) {
  if (typeof room.setPlayerDiscProperties === "function") {
    room.setPlayerDiscProperties(playerId, properties);
    return;
  }
  room.setDiscProperties(playerId, 1, properties, 0);
}

function setBallDisc(room, properties) {
  if (room.setDiscProperties.length >= 4) {
    room.setDiscProperties(0, 0, properties, 0);
    return;
  }
  room.setDiscProperties(0, properties);
}

function zeroInputs(room, bots) {
  for (const bot of bots) {
    try {
      room.playerInput(Utils.keyState(0, 0, false), bot.id);
    } catch (_) {}
  }
}

function releaseKickoff(room, bots) {
  const redSt = bots.find((bot) => bot.teamId === 1 && bot.role === "st");
  const blueSt = bots.find((bot) => bot.teamId === 2 && bot.role === "st");
  if (!redSt || !blueSt) throw new Error("ST missing while releasing kickoff");

  setBallDisc(room, { x: 0, y: 0, xspeed: 0, yspeed: 0 });
  setPlayerDisc(room, redSt.id, { x: -24, y: 0, xspeed: 0, yspeed: 0 });
  setPlayerDisc(room, blueSt.id, { x: 24, y: 0, xspeed: 0, yspeed: 0 });
  room.playerInput(Utils.keyState(1, 0, true), redSt.id);
  room.playerInput(Utils.keyState(-1, 0, true), blueSt.id);
  room.runSteps(24);
  zeroInputs(room, bots);
  room.runSteps(2);
}

function transformDisc(source, mirror) {
  return {
    x: mirror ? -Number(source.x || 0) : Number(source.x || 0),
    y: Number(source.y || 0),
    xspeed: mirror ? -Number(source.vx || 0) : Number(source.vx || 0),
    yspeed: Number(source.vy || 0),
  };
}

function applyReplayScenario(room, bots, scenario, eliteTeamId) {
  if (!scenario?.teams?.["1"] || !scenario?.teams?.["2"]) {
    throw new Error("scenario missing team states");
  }

  const mirror = Number(eliteTeamId) === 2;
  const baselineTeamId = mirror ? 1 : 2;

  for (const bot of bots) {
    const isElite = Boolean(bot.isElite);
    const sourceTeamId = isElite ? 1 : 2;
    const targetTeamId = isElite ? eliteTeamId : baselineTeamId;
    if (Number(bot.teamId) !== Number(targetTeamId)) {
      throw new Error("scenario bot team mismatch");
    }
    const source = scenario.teams[String(sourceTeamId)]?.[bot.role];
    if (!source) {
      throw new Error(
        "scenario missing source role " + sourceTeamId + "/" + bot.role,
      );
    }
    setPlayerDisc(room, bot.id, transformDisc(source, mirror));
  }

  setBallDisc(room, transformDisc(scenario.ball, mirror));
  zeroInputs(room, bots);
  room.runSteps(2);
}

function prepareReplayScenario(room, bots, scenario, eliteTeamId) {
  releaseKickoff(room, bots);
  applyReplayScenario(room, bots, scenario, eliteTeamId);
}

function primeElitePolicy(policy, eliteBots, scenario) {
  policy.reset();
  const history = scenario.history || [];
  for (const frame of history) {
    for (const bot of eliteBots) {
      const features = frame.features?.["1"]?.[bot.role];
      if (!features) {
        throw new Error(
          "scenario history missing elite warmup features for " + bot.role,
        );
      }
      policy.act({
        agent_id: String(bot.id),
        role: bot.role,
        features,
      });
    }
  }
}

module.exports = {
  ROLE_STARTS,
  setPlayerDisc,
  setBallDisc,
  releaseKickoff,
  transformDisc,
  applyReplayScenario,
  prepareReplayScenario,
  primeElitePolicy,
};
