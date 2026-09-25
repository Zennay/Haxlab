"use strict";

const { discOf, num } = require("./elite_features");

function discreteDirection(delta, deadzone = 5) {
  if (delta > deadzone) return 1;
  if (delta < -deadzone) return -1;
  return 0;
}

function isKickoffState(gameState) {
  const ball = gameState?.physicsState?.discs?.[0];
  if (!ball?.pos) return false;
  const speedX = num(ball.speed?.x);
  const speedY = num(ball.speed?.y);
  return (
    Math.abs(num(ball.pos.x)) <= 8 &&
    Math.abs(num(ball.pos.y)) <= 8 &&
    Math.hypot(speedX, speedY) <= 0.35
  );
}

function kickoffAction(role, player, gameState) {
  if (!isKickoffState(gameState)) return null;

  const disc = discOf(player);
  const ball = gameState?.physicsState?.discs?.[0];
  if (!disc?.pos || !ball?.pos) return null;

  // Keep the defensive spine intact. Only ST is responsible for actually
  // breaking the kickoff deadlock. The non-kickoff ST will simply be stopped
  // by HaxBall's kickoff barrier until the opponent moves the ball.
  if (String(role).toLowerCase() !== "st") {
    return { dirX: 0, dirY: 0, kick: false, source: "kickoff_shape_hold" };
  }

  const dx = num(ball.pos.x) - num(disc.pos.x);
  const dy = num(ball.pos.y) - num(disc.pos.y);
  const distance = Math.hypot(dx, dy);

  return {
    dirX: discreteDirection(dx),
    dirY: discreteDirection(dy),
    kick: distance <= 30,
    source: "kickoff_st",
  };
}

function enforceKickRange(action, player, gameState, maxDistance = 32) {
  if (!action) return action;
  const disc = discOf(player);
  const ball = gameState?.physicsState?.discs?.[0];
  if (!disc?.pos || !ball?.pos) {
    return { ...action, kick: false };
  }
  const dx = num(ball.pos.x) - num(disc.pos.x);
  const dy = num(ball.pos.y) - num(disc.pos.y);
  const inRange = Math.hypot(dx, dy) <= maxDistance;
  return {
    ...action,
    kick: Boolean(action.kick) && inRange,
    kick_in_range: inRange,
  };
}

module.exports = {
  discreteDirection,
  isKickoffState,
  kickoffAction,
  enforceKickRange,
};
