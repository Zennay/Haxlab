#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const initAPI = require("node-haxball");

const API = initAPI();
const { Room, Utils } = API;
const { ElitePolicyRuntime } = require("./elite_policy_runtime");
const {
  discOf,
  statePlayers,
  buildFeatureObject,
  canonicalActionToWorld,
  num,
} = require("./elite_features");
const { kickoffAction, enforceKickRange } = require("./elite_tactics");

const ROLES = ["gk", "dm", "am", "st"];
const BASELINE_PROFILES = ["balanced", "compact", "press"];

function usage() {
  console.error(
    "Usage: node tools/elite_sandbox_benchmark.js " +
      "--model runtime-model.json --stadium stadium.hbs " +
      "[--matches 12] [--minutes 3] [--sample-every 6] [--output result.json]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const result = { matches: 12, minutes: 3, sampleEvery: 6, output: null };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--model") { result.model = value; i += 1; }
    else if (key === "--stadium") { result.stadium = value; i += 1; }
    else if (key === "--matches") { result.matches = Number(value); i += 1; }
    else if (key === "--minutes") { result.minutes = Number(value); i += 1; }
    else if (key === "--sample-every") { result.sampleEvery = Number(value); i += 1; }
    else if (key === "--output") { result.output = value; i += 1; }
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("Unknown argument: " + key);
  }
  if (!result.model || !result.stadium) usage();
  result.matches = Math.max(2, Math.floor(result.matches));
  result.minutes = Math.max(0.25, Number(result.minutes) || 3);
  result.sampleEvery = Math.max(1, Math.floor(result.sampleEvery));
  return result;
}

function canonicalPosition(player, teamId) {
  const disc = discOf(player);
  if (!disc?.pos) return { x: 0, y: 0 };
  const sign = teamId === 1 ? 1 : -1;
  return { x: sign * num(disc.pos.x), y: num(disc.pos.y) };
}

function worldTarget(teamId, canonicalX, y) {
  const sign = teamId === 1 ? 1 : -1;
  return { x: sign * canonicalX, y };
}

function discreteDirection(delta, deadzone = 6) {
  if (delta > deadzone) return 1;
  if (delta < -deadzone) return -1;
  return 0;
}

function scriptedTarget(role, player, ball, teamId, profile, stadium) {
  const sign = teamId === 1 ? 1 : -1;
  const canonicalBallX = sign * num(ball.pos.x);
  const ballY = num(ball.pos.y);
  const width = Number(stadium.width || 420);
  const ownGoalX = -Math.max(160, width * 0.82);
  const playerPos = canonicalPosition(player, teamId);

  let targetX = playerPos.x;
  let targetY = ballY;

  if (role === "gk") {
    targetX = ownGoalX + 18;
    targetY = Math.max(-70, Math.min(70, ballY * 0.50));
    if (canonicalBallX < ownGoalX + 110) {
      targetX = Math.min(ownGoalX + 72, canonicalBallX - 10);
      targetY = ballY * 0.72;
    }
  } else if (role === "dm") {
    const offset = profile === "press" ? 62 : profile === "compact" ? 118 : 90;
    targetX = Math.max(ownGoalX + 78, canonicalBallX - offset);
    targetY = ballY * (profile === "compact" ? 0.35 : 0.60);
  } else if (role === "am") {
    const offset = profile === "press" ? 12 : profile === "compact" ? 52 : 30;
    targetX = canonicalBallX - offset;
    targetY = ballY * 0.80;
  } else {
    const ahead = profile === "compact" ? 38 : profile === "press" ? 18 : 58;
    targetX = Math.min(width - 55, canonicalBallX + ahead);
    targetY = ballY * 0.70;
  }

  const laneBias = { gk: 0, dm: -18, am: 16, st: 0 }[role] || 0;
  targetY += laneBias;
  return worldTarget(teamId, targetX, targetY);
}

function scriptedAction(role, player, ball, teamId, profile, stadium) {
  const disc = discOf(player);
  if (!disc?.pos || !ball?.pos) return { dirX: 0, dirY: 0, kick: false };

  const target = scriptedTarget(role, player, ball, teamId, profile, stadium);
  const dx = target.x - num(disc.pos.x);
  const dy = target.y - num(disc.pos.y);
  const ballDx = num(ball.pos.x) - num(disc.pos.x);
  const ballDy = num(ball.pos.y) - num(disc.pos.y);
  const distanceToBall = Math.hypot(ballDx, ballDy);

  return {
    dirX: discreteDirection(dx),
    dirY: discreteDirection(dy),
    kick: distanceToBall <= 29,
  };
}

function addPlayer(room, id, name, teamId) {
  room.playerJoin(id, name, "xx", "AI", "benchmark-" + id, "benchmark-" + id);
  room.setPlayerTeam(id, teamId, 0);
}

function scoreFromGameState(gameState) {
  return {
    red: num(
      gameState?.redScore ??
      gameState?.scoreRed ??
      gameState?.scores?.red ??
      gameState?.scores?.[1],
    ),
    blue: num(
      gameState?.blueScore ??
      gameState?.scoreBlue ??
      gameState?.scores?.blue ??
      gameState?.scores?.[2],
    ),
  };
}

function runMatch({
  runtimeModel,
  stadium,
  eliteTeamId,
  baselineProfile,
  minutes,
  sampleEvery,
  matchIndex,
}) {
  const policy = new ElitePolicyRuntime(runtimeModel);
  let redGoals = 0;
  let blueGoals = 0;
  let goalEvents = 0;
  let runtimeErrors = 0;

  const room = Room.sandbox(
    {
      onTeamGoal: (teamId) => {
        goalEvents += 1;
        if (Number(teamId) === 1) redGoals += 1;
        else if (Number(teamId) === 2) blueGoals += 1;
      },
    },
    { controlledPlayerId: 0 },
  );

  room.setSimulationSpeed(0);
  room.setCurrentStadium(stadium, 0);
  room.setScoreLimit(0, 0);
  room.setTimeLimit(0, 0);

  const baselineTeamId = eliteTeamId === 1 ? 2 : 1;
  const eliteBots = [];
  const baselineBots = [];

  for (let index = 0; index < 4; index += 1) {
    const role = ROLES[index];
    const eliteId = 100 + index;
    const baselineId = 200 + index;
    addPlayer(room, eliteId, "Elite-" + role.toUpperCase(), eliteTeamId);
    addPlayer(room, baselineId, "Script-" + role.toUpperCase(), baselineTeamId);
    eliteBots.push({ id: eliteId, role, keyState: 0, kicks: 0, actions: 0 });
    baselineBots.push({ id: baselineId, role, keyState: 0, kicks: 0, actions: 0 });
  }

  room.startGame(0);
  room.runSteps(5);

  const totalTicks = Math.floor(minutes * 60 * 60);
  const metrics = {
    eliteBallHalfTicks: 0,
    baselineBallHalfTicks: 0,
    neutralBallTicks: 0,
    eliteAttackThirdTicks: 0,
    baselineAttackThirdTicks: 0,
    sampledTicks: 0,
    directionCounts: {},
    roleCanonicalXSum: { gk: 0, dm: 0, am: 0, st: 0 },
    roleCanonicalXSamples: { gk: 0, dm: 0, am: 0, st: 0 },
    roleBallDistanceSum: { gk: 0, dm: 0, am: 0, st: 0 },
    roleBallDistanceSamples: { gk: 0, dm: 0, am: 0, st: 0 },
    roleNearBallSamples: { gk: 0, dm: 0, am: 0, st: 0 },
  };

  for (let tick = 0; tick < totalTicks; tick += 1) {
    if (tick % sampleEvery === 0) {
      const state = room.state;
      const gameState = room.gameState;
      const ball = gameState?.physicsState?.discs?.[0];
      if (state && gameState && ball?.pos) {
        const players = statePlayers(state);

        for (const bot of eliteBots) {
          const player = state.getPlayer?.(bot.id) ||
            players.find((candidate) => Number(candidate.id) === bot.id);
          if (!player || !discOf(player)?.pos) continue;
          try {
            const tactical = kickoffAction(bot.role, player, gameState);
            let action;
            if (tactical) {
              action = tactical;
            } else {
              const features = buildFeatureObject(player, state, gameState);
              if (!features) continue;
              const canonical = policy.act({
                agent_id: String(bot.id),
                role: bot.role,
                features,
              });
              action = canonicalActionToWorld(canonical, eliteTeamId);
            }
            action = enforceKickRange(action, player, gameState);

            const playerDisc = discOf(player);
            const canonicalX =
              (eliteTeamId === 1 ? 1 : -1) * num(playerDisc?.pos?.x);
            metrics.roleCanonicalXSum[bot.role] += canonicalX;
            metrics.roleCanonicalXSamples[bot.role] += 1;

            const ballDx = num(ball.pos.x) - num(playerDisc?.pos?.x);
            const ballDy = num(ball.pos.y) - num(playerDisc?.pos?.y);
            const ballDistance = Math.hypot(ballDx, ballDy);
            metrics.roleBallDistanceSum[bot.role] += ballDistance;
            metrics.roleBallDistanceSamples[bot.role] += 1;
            if (ballDistance <= 32) metrics.roleNearBallSamples[bot.role] += 1;

            const canonicalDirX =
              eliteTeamId === 2 ? -Number(action.dirX || 0) : Number(action.dirX || 0);
            const directionKey =
              String(canonicalDirX) + "," + String(Number(action.dirY || 0));
            metrics.directionCounts[directionKey] =
              (metrics.directionCounts[directionKey] || 0) + 1;

            const keyState = Utils.keyState(action.dirX, action.dirY, action.kick);
            room.playerInput(keyState, bot.id);
            bot.keyState = keyState;
            bot.actions += 1;
            if (action.kick) bot.kicks += 1;
          } catch (error) {
            runtimeErrors += 1;
            if (runtimeErrors <= 3) {
              console.error(
                "match " + matchIndex + " elite runtime error:",
                error?.stack || String(error),
              );
            }
          }
        }

        for (const bot of baselineBots) {
          const player = state.getPlayer?.(bot.id) ||
            players.find((candidate) => Number(candidate.id) === bot.id);
          if (!player || !discOf(player)?.pos) continue;
          const action = scriptedAction(
            bot.role,
            player,
            ball,
            baselineTeamId,
            baselineProfile,
            stadium,
          );
          const keyState = Utils.keyState(action.dirX, action.dirY, action.kick);
          room.playerInput(keyState, bot.id);
          bot.keyState = keyState;
          bot.actions += 1;
          if (action.kick) bot.kicks += 1;
        }

        const eliteAxisX = (eliteTeamId === 1 ? 1 : -1) * num(ball.pos.x);
        metrics.sampledTicks += 1;
        if (eliteAxisX > 10) metrics.eliteBallHalfTicks += 1;
        else if (eliteAxisX < -10) metrics.baselineBallHalfTicks += 1;
        else metrics.neutralBallTicks += 1;

        const attackThird = Math.max(110, Number(stadium.width || 420) * 0.34);
        if (eliteAxisX > attackThird) metrics.eliteAttackThirdTicks += 1;
        else if (eliteAxisX < -attackThird) metrics.baselineAttackThirdTicks += 1;
      }
    }
    room.runSteps(1);
  }

  const finalScore = scoreFromGameState(room.gameState);
  if (goalEvents === 0 && (finalScore.red > 0 || finalScore.blue > 0)) {
    redGoals = finalScore.red;
    blueGoals = finalScore.blue;
  }

  const eliteGoals = eliteTeamId === 1 ? redGoals : blueGoals;
  const baselineGoals = eliteTeamId === 1 ? blueGoals : redGoals;
  const sampled = Math.max(1, metrics.sampledTicks);

  const result = {
    match_index: matchIndex,
    elite_team_id: eliteTeamId,
    baseline_team_id: baselineTeamId,
    baseline_profile: baselineProfile,
    minutes,
    ticks: totalTicks,
    goals: {
      elite: eliteGoals,
      baseline: baselineGoals,
      red: redGoals,
      blue: blueGoals,
    },
    result:
      eliteGoals > baselineGoals ? "win" :
      eliteGoals < baselineGoals ? "loss" : "draw",
    territory: {
      elite_half_rate: metrics.eliteBallHalfTicks / sampled,
      baseline_half_rate: metrics.baselineBallHalfTicks / sampled,
      neutral_rate: metrics.neutralBallTicks / sampled,
      elite_attack_third_rate: metrics.eliteAttackThirdTicks / sampled,
      baseline_attack_third_rate: metrics.baselineAttackThirdTicks / sampled,
    },
    policy: {
      runtime_errors: runtimeErrors,
      total_actions: eliteBots.reduce((sum, bot) => sum + bot.actions, 0),
      total_kicks: eliteBots.reduce((sum, bot) => sum + bot.kicks, 0),
      roles: Object.fromEntries(
        eliteBots.map((bot) => [
          bot.role,
          {
            actions: bot.actions,
            kicks: bot.kicks,
            average_canonical_x:
              metrics.roleCanonicalXSum[bot.role] /
              Math.max(1, metrics.roleCanonicalXSamples[bot.role]),
            average_ball_distance:
              metrics.roleBallDistanceSum[bot.role] /
              Math.max(1, metrics.roleBallDistanceSamples[bot.role]),
            near_ball_rate:
              metrics.roleNearBallSamples[bot.role] /
              Math.max(1, metrics.roleBallDistanceSamples[bot.role]),
          },
        ]),
      ),
      direction_counts: metrics.directionCounts,
    },
    baseline: {
      total_actions: baselineBots.reduce((sum, bot) => sum + bot.actions, 0),
      total_kicks: baselineBots.reduce((sum, bot) => sum + bot.kicks, 0),
    },
  };

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}
  return result;
}

function summarize(matches, stadium, modelPath) {
  const wins = matches.filter((match) => match.result === "win").length;
  const losses = matches.filter((match) => match.result === "loss").length;
  const draws = matches.length - wins - losses;
  const eliteGoals = matches.reduce((sum, match) => sum + match.goals.elite, 0);
  const baselineGoals = matches.reduce((sum, match) => sum + match.goals.baseline, 0);
  const runtimeErrors = matches.reduce(
    (sum, match) => sum + match.policy.runtime_errors,
    0,
  );
  const avg = (selector) =>
    matches.reduce((sum, match) => sum + selector(match), 0) /
    Math.max(1, matches.length);

  const bySide = {};
  for (const teamId of [1, 2]) {
    const rows = matches.filter((match) => match.elite_team_id === teamId);
    if (!rows.length) continue;
    bySide[String(teamId)] = {
      matches: rows.length,
      wins: rows.filter((match) => match.result === "win").length,
      losses: rows.filter((match) => match.result === "loss").length,
      draws: rows.filter((match) => match.result === "draw").length,
      elite_goals: rows.reduce((sum, match) => sum + match.goals.elite, 0),
      baseline_goals: rows.reduce((sum, match) => sum + match.goals.baseline, 0),
      elite_half_rate:
        rows.reduce((sum, match) => sum + match.territory.elite_half_rate, 0) /
        rows.length,
      baseline_half_rate:
        rows.reduce((sum, match) => sum + match.territory.baseline_half_rate, 0) /
        rows.length,
      elite_attack_third_rate:
        rows.reduce(
          (sum, match) => sum + match.territory.elite_attack_third_rate,
          0,
        ) / rows.length,
      baseline_attack_third_rate:
        rows.reduce(
          (sum, match) => sum + match.territory.baseline_attack_third_rate,
          0,
        ) / rows.length,
      total_kicks: rows.reduce(
        (sum, match) => sum + match.policy.total_kicks,
        0,
      ),
    };
  }

  const byProfile = {};
  for (const profile of BASELINE_PROFILES) {
    const rows = matches.filter((match) => match.baseline_profile === profile);
    if (!rows.length) continue;
    byProfile[profile] = {
      matches: rows.length,
      wins: rows.filter((match) => match.result === "win").length,
      losses: rows.filter((match) => match.result === "loss").length,
      draws: rows.filter((match) => match.result === "draw").length,
      elite_goals: rows.reduce((sum, match) => sum + match.goals.elite, 0),
      baseline_goals: rows.reduce((sum, match) => sum + match.goals.baseline, 0),
    };
  }

  return {
    schema: "haxlab-elite-sandbox-benchmark-v1",
    model_path: modelPath,
    stadium: {
      name: stadium.name || null,
      width: stadium.width ?? null,
      height: stadium.height ?? null,
    },
    matches: matches.length,
    wins,
    draws,
    losses,
    win_rate: wins / Math.max(1, matches.length),
    non_loss_rate: (wins + draws) / Math.max(1, matches.length),
    goals: {
      elite: eliteGoals,
      baseline: baselineGoals,
      differential: eliteGoals - baselineGoals,
      per_match_elite: eliteGoals / Math.max(1, matches.length),
      per_match_baseline: baselineGoals / Math.max(1, matches.length),
    },
    territory: {
      elite_half_rate: avg((match) => match.territory.elite_half_rate),
      baseline_half_rate: avg((match) => match.territory.baseline_half_rate),
      elite_attack_third_rate: avg(
        (match) => match.territory.elite_attack_third_rate,
      ),
      baseline_attack_third_rate: avg(
        (match) => match.territory.baseline_attack_third_rate,
      ),
    },
    runtime_errors: runtimeErrors,
    by_elite_side: bySide,
    by_profile: byProfile,
    match_results: matches,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const runtimeModel = JSON.parse(fs.readFileSync(args.model, "utf8"));
  const stadiumJson = JSON.parse(fs.readFileSync(args.stadium, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));

  const matches = [];
  for (let index = 0; index < args.matches; index += 1) {
    const eliteTeamId = index % 2 === 0 ? 1 : 2;
    const profile = BASELINE_PROFILES[
      Math.floor(index / 2) % BASELINE_PROFILES.length
    ];
    const result = runMatch({
      runtimeModel,
      stadium,
      eliteTeamId,
      baselineProfile: profile,
      minutes: args.minutes,
      sampleEvery: args.sampleEvery,
      matchIndex: index + 1,
    });
    matches.push(result);
    console.error(
      "[" + (index + 1) + "/" + args.matches + "] " +
      profile + " side=" + eliteTeamId + " " +
      result.goals.elite + "-" + result.goals.baseline + " " + result.result,
    );
  }

  const summary = summarize(matches, stadium, args.model);
  const rendered = JSON.stringify(summary, null, 2) + "\n";
  if (args.output) {
    fs.mkdirSync(path.dirname(args.output), { recursive: true });
    fs.writeFileSync(args.output, rendered);
  }
  process.stdout.write(rendered);
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}

module.exports = {
  BASELINE_PROFILES,
  discreteDirection,
  scriptedTarget,
  scriptedAction,
  runMatch,
  summarize,
};
