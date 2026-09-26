"use strict";

const FEATURE_NAMES = Object.freeze([
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
]);

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function discOf(player) {
  return player?.disc?.ext || player?.disc || null;
}

function statePlayers(state) {
  if (Array.isArray(state?.players)) return state.players;
  if (Array.isArray(state?.playerList)) return state.playerList;
  return [];
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

function teamAttackSign(teamId) {
  return Number(teamId) === 1 ? 1 : Number(teamId) === 2 ? -1 : 0;
}

function lineOrderedVectors(origin, candidates, sign, limit) {
  const ownDisc = discOf(origin);
  if (!ownDisc?.pos) return Array(limit * 5).fill(0);

  const ranked = candidates
    .filter((candidate) => discOf(candidate)?.pos)
    .slice()
    .sort((a, b) => {
      const aDisc = discOf(a);
      const bDisc = discOf(b);
      const aTeam = Number(a.team?.id || a.teamId || 0);
      const bTeam = Number(b.team?.id || b.teamId || 0);
      const aAxis = teamAttackSign(aTeam) * num(aDisc.pos.x);
      const bAxis = teamAttackSign(bTeam) * num(bDisc.pos.x);
      if (aAxis !== bAxis) return aAxis - bAxis;
      return Number(a.id || 0) - Number(b.id || 0);
    })
    .slice(0, limit);

  const result = [];
  for (const candidate of ranked) {
    result.push(...entityVector(origin, candidate, sign));
  }
  while (result.length < limit * 5) result.push(0, 0, 0, 0, 0);
  return result;
}

function scoreDiffFromGameState(gameState, teamId) {
  const red = num(
    gameState?.redScore ??
      gameState?.scoreRed ??
      gameState?.scores?.red ??
      gameState?.scores?.[1],
  );
  const blue = num(
    gameState?.blueScore ??
      gameState?.scoreBlue ??
      gameState?.scores?.blue ??
      gameState?.scores?.[2],
  );
  return Number(teamId) === 1 ? red - blue : blue - red;
}

function buildFeatureObject(player, state, gameState) {
  const ownDisc = discOf(player);
  const ballDisc = gameState?.physicsState?.discs?.[0];
  if (!ownDisc?.pos || !ballDisc?.pos) return null;

  const teamId = Number(player.team?.id || player.teamId || 0);
  if (!(teamId === 1 || teamId === 2)) return null;

  const sign = teamId === 1 ? 1 : -1;
  const players = statePlayers(state);
  const teammates = players.filter(
    (candidate) =>
      candidate.id !== player.id &&
      Number(candidate.team?.id || candidate.teamId || 0) === teamId &&
      discOf(candidate)?.pos,
  );
  const opponents = players.filter(
    (candidate) =>
      Number(candidate.team?.id || candidate.teamId || 0) === 3 - teamId &&
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
    ...lineOrderedVectors(player, teammates, sign, 3),
    ...lineOrderedVectors(player, opponents, sign, 4),
    scoreDiffFromGameState(gameState, teamId),
  ];

  return Object.fromEntries(
    FEATURE_NAMES.map((name, index) => [name, values[index]]),
  );
}

function canonicalActionToWorld(action, teamId) {
  const canonicalDirX = Math.max(
    -1,
    Math.min(1, Number(action?.dir_x) || 0),
  );
  return {
    dirX: Number(teamId) === 2 ? -canonicalDirX : canonicalDirX,
    dirY: Math.max(-1, Math.min(1, Number(action?.dir_y) || 0)),
    kick: Boolean(action?.kick),
  };
}

module.exports = {
  FEATURE_NAMES,
  num,
  discOf,
  statePlayers,
  entityVector,
  teamAttackSign,
  lineOrderedVectors,
  scoreDiffFromGameState,
  buildFeatureObject,
  canonicalActionToWorld,
};