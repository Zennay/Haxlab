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

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireFiniteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite native number`);
  }
  return value;
}

function validateDiscState(source, label = "scenario disc") {
  if (!isPlainObject(source)) {
    throw new Error(`${label} must be an object`);
  }
  return {
    x: requireFiniteNumber(source.x, `${label}.x`),
    y: requireFiniteNumber(source.y, `${label}.y`),
    vx: requireFiniteNumber(source.vx, `${label}.vx`),
    vy: requireFiniteNumber(source.vy, `${label}.vy`),
  };
}

function transformDisc(source, mirror, label = "scenario disc") {
  if (typeof mirror !== "boolean") {
    throw new Error("scenario mirror flag must be boolean");
  }
  const disc = validateDiscState(source, label);
  return {
    x: mirror ? -disc.x : disc.x,
    y: disc.y,
    xspeed: mirror ? -disc.vx : disc.vx,
    yspeed: disc.vy,
  };
}

function applyReplayScenario(room, bots, scenario, eliteTeamId) {
  if (!isPlainObject(scenario)) {
    throw new Error("scenario must be an object");
  }
  if (!isPlainObject(scenario.teams?.["1"]) || !isPlainObject(scenario.teams?.["2"])) {
    throw new Error("scenario missing team states");
  }
  if (
    typeof eliteTeamId !== "number" ||
    !Number.isInteger(eliteTeamId) ||
    (eliteTeamId !== 1 && eliteTeamId !== 2)
  ) {
    throw new Error("eliteTeamId must be native team id 1 or 2");
  }

  const mirror = eliteTeamId === 2;
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
    setPlayerDisc(
      room,
      bot.id,
      transformDisc(source, mirror, `scenario team ${sourceTeamId} role ${bot.role}`),
    );
  }

  setBallDisc(room, transformDisc(scenario.ball, mirror, "scenario ball"));
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
  isPlainObject,
  requireFiniteNumber,
  validateDiscState,
  transformDisc,
  applyReplayScenario,
  prepareReplayScenario,
  primeElitePolicy,
};
