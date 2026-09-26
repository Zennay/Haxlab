"use strict";

const { discOf, num } = require("./elite_features");

function discreteDirection(delta, deadzone = 5) {
  if (delta > deadzone) return 1;
  if (delta < -deadzone) return -1;
  return 0;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function recoveryAction(
  role,
  player,
  gameState,
  teamId,
  {
    stadiumWidth = 800,
  } = {},
) {
  const disc = discOf(player);
  const ball = gameState?.physicsState?.discs?.[0];
  if (!disc?.pos || !ball?.pos) return null;

  const sign = Number(teamId) === 2 ? -1 : 1;
  const width = Math.max(420, Number(stadiumWidth) || 800);
  const canonicalBallX = sign * num(ball.pos.x);
  const canonicalPlayerX = sign * num(disc.pos.x);
  const ballY = num(ball.pos.y);
  const roleKey = String(role).toLowerCase();

  let targetX;
  let targetY;

  if (roleKey === "gk") {
    targetX = clamp(canonicalBallX - 420, -0.90 * width, -0.52 * width);
    targetY = clamp(ballY * 0.35, -95, 95);
  } else if (roleKey === "dm") {
    targetX = clamp(canonicalBallX - 180, -0.68 * width, 0.24 * width);
    targetY = clamp(ballY * 0.55 - 18, -150, 150);
  } else if (roleKey === "am") {
    targetX = clamp(canonicalBallX - 45, -0.34 * width, 0.56 * width);
    targetY = clamp(ballY * 0.75 + 16, -170, 170);
  } else {
    targetX = clamp(canonicalBallX + 135, -0.12 * width, 0.82 * width);
    targetY = clamp(ballY * 0.82, -175, 175);
  }

  const targetWorldX = sign * targetX;
  const dx = targetWorldX - num(disc.pos.x);
  const dy = targetY - num(disc.pos.y);
  const ballDx = num(ball.pos.x) - num(disc.pos.x);
  const ballDy = num(ball.pos.y) - num(disc.pos.y);
  const ballDistance = Math.hypot(ballDx, ballDy);

  return {
    dirX: discreteDirection(dx, 7),
    dirY: discreteDirection(dy, 7),
    kick: ballDistance <= 30,
    source: "closed_loop_recovery",
    target_canonical_x: targetX,
    target_y: targetY,
    canonical_player_x: canonicalPlayerX,
    ball_distance: ballDistance,
  };
}

function shouldRecoverFromStall(
  action,
  ballDistance,
  stallStreak,
  {
    minimumBallDistance = 120,
    minimumStallDecisions = 3,
  } = {},
) {
  const stationary =
    Number(action?.dirX || 0) === 0 &&
    Number(action?.dirY || 0) === 0;
  return (
    stationary &&
    Number(ballDistance) >= minimumBallDistance &&
    Number(stallStreak) >= minimumStallDecisions
  );
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
  recoveryAction,
  shouldRecoverFromStall,
};
