"use strict";

const initAPI = require("node-haxball");
const { Utils } = initAPI();

const ROLE_STARTS = Object.freeze({
  gk: { x: -620, y: 0 },
  dm: { x: -280, y: -62 },
  am: { x: 70, y: 58 },
  st: { x: 270, y: 0 },
});

function rawSetPlayerDisc(room, playerId, properties) {
  if (typeof room.setPlayerDiscProperties === "function") {
    room.setPlayerDiscProperties(playerId, properties);
    return;
  }
  room.setDiscProperties(playerId, 1, properties, 0);
}

function rawSetBallDisc(room, properties) {
  // Room.sandbox exposes the low-level signature:
  // setDiscProperties(discId, discType, properties, byId).
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

function releaseInitialKickoff(room, bots) {
  const redSt = bots.find((bot) => bot.teamId === 1 && bot.role === "st");
  const blueSt = bots.find((bot) => bot.teamId === 2 && bot.role === "st");
  if (!redSt || !blueSt) {
    throw new Error("neutral benchmark start requires ST on both teams");
  }

  rawSetBallDisc(room, {
    x: 0,
    y: 0,
    xspeed: 0,
    yspeed: 0,
  });
  rawSetPlayerDisc(room, redSt.id, {
    x: -24,
    y: 0,
    xspeed: 0,
    yspeed: 0,
  });
  rawSetPlayerDisc(room, blueSt.id, {
    x: 24,
    y: 0,
    xspeed: 0,
    yspeed: 0,
  });

  // Only the team that actually owns kickoff can enter/touch. Driving both STs
  // toward the ball makes this independent of which side node-haxball assigns.
  room.playerInput(Utils.keyState(1, 0, true), redSt.id);
  room.playerInput(Utils.keyState(-1, 0, true), blueSt.id);
  room.runSteps(24);
  zeroInputs(room, bots);
  room.runSteps(2);
}

function resetNeutral4v4(room, bots, pairIndex = 0) {
  for (const bot of bots) {
    const start = ROLE_STARTS[bot.role];
    if (!start) throw new Error("unknown neutral-start role: " + bot.role);
    const sign = bot.teamId === 1 ? 1 : -1;
    rawSetPlayerDisc(room, bot.id, {
      x: sign * start.x,
      y: start.y,
      xspeed: 0,
      yspeed: 0,
    });
  }

  // Mirror the ball velocity between paired Red/Blue evaluations. In canonical
  // elite coordinates the initial state is therefore identical for both sides.
  const worldDirection = pairIndex % 2 === 0 ? 1 : -1;
  const verticalDirection = Math.floor(pairIndex / 2) % 2 === 0 ? 1 : -1;
  rawSetBallDisc(room, {
    x: 0,
    y: 0,
    xspeed: 0.60 * worldDirection,
    yspeed: 0.12 * verticalDirection,
  });
  zeroInputs(room, bots);
  room.runSteps(2);
}

function prepareNeutralStart(room, bots, pairIndex = 0) {
  releaseInitialKickoff(room, bots);
  resetNeutral4v4(room, bots, pairIndex);
}

module.exports = {
  ROLE_STARTS,
  rawSetPlayerDisc,
  rawSetBallDisc,
  releaseInitialKickoff,
  resetNeutral4v4,
  prepareNeutralStart,
};
