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

const ROLES = ["gk", "dm", "am", "st"];

function usage() {
  console.error(
    "Usage: node tools/elite_model_duel.js " +
      "--challenger challenger.json --champion champion.json " +
      "--stadium stadium.hbs [--matches 12] [--minutes 2] [--seed 1337] " +
      "[--sample-every 6] [--output result.json]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const result = {
    matches: 12,
    minutes: 2,
    seed: 1337,
    sampleEvery: 6,
    output: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--challenger") { result.challenger = value; i += 1; }
    else if (key === "--champion") { result.champion = value; i += 1; }
    else if (key === "--stadium") { result.stadium = value; i += 1; }
    else if (key === "--matches") { result.matches = Number(value); i += 1; }
    else if (key === "--minutes") { result.minutes = Number(value); i += 1; }
    else if (key === "--seed") { result.seed = Number(value); i += 1; }
    else if (key === "--sample-every") { result.sampleEvery = Number(value); i += 1; }
    else if (key === "--output") { result.output = value; i += 1; }
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("Unknown argument: " + key);
  }
  if (!result.challenger || !result.champion || !result.stadium) usage();
  result.matches = Math.max(2, Math.floor(result.matches));
  result.minutes = Math.max(0.25, Number(result.minutes) || 2);
  result.seed = Number.isFinite(result.seed) ? Math.floor(result.seed) : 1337;
  result.sampleEvery = Math.max(1, Math.floor(result.sampleEvery));
  return result;
}

function makeRng(seed) {
  let state = (seed >>> 0) || 1;
  return function rng() {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function addPlayer(room, id, name, teamId) {
  room.playerJoin(
    id,
    name,
    "xx",
    "AI",
    "duel-" + id,
    "duel-" + id,
  );
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

function canonicalPosition(player, teamId) {
  const disc = discOf(player);
  if (!disc?.pos) return 0;
  return (teamId === 1 ? 1 : -1) * num(disc.pos.x);
}

function runMatch({
  challengerModel,
  championModel,
  stadium,
  challengerTeamId,
  minutes,
  sampleEvery,
  matchIndex,
  seed,
}) {
  const challenger = new ElitePolicyRuntime(challengerModel);
  const champion = new ElitePolicyRuntime(championModel);
  const championTeamId = challengerTeamId === 1 ? 2 : 1;

  let redGoals = 0;
  let blueGoals = 0;
  let goalEvents = 0;
  let challengerErrors = 0;
  let championErrors = 0;

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

  const challengerBots = [];
  const championBots = [];
  for (let index = 0; index < 4; index += 1) {
    const role = ROLES[index];
    const challengerId = 100 + index;
    const championId = 200 + index;
    addPlayer(
      room,
      challengerId,
      "Challenger-" + role.toUpperCase(),
      challengerTeamId,
    );
    addPlayer(
      room,
      championId,
      "Champion-" + role.toUpperCase(),
      championTeamId,
    );
    challengerBots.push({
      id: challengerId,
      role,
      actions: 0,
      kicks: 0,
      xSum: 0,
      xSamples: 0,
    });
    championBots.push({
      id: championId,
      role,
      actions: 0,
      kicks: 0,
      xSum: 0,
      xSamples: 0,
    });
  }

  room.startGame(0);
  room.runSteps(5);

  const rng = makeRng(seed);
  const startX = (rng() * 2 - 1) * Math.min(120, Number(stadium.width || 800) * 0.15);
  const startY = (rng() * 2 - 1) * Math.min(90, Number(stadium.height || 350) * 0.22);
  const startVx = (rng() * 2 - 1) * 0.8;
  const startVy = (rng() * 2 - 1) * 0.8;
  room.setDiscProperties(
    0,
    0,
    { x: startX, y: startY, xspeed: startVx, yspeed: startVy },
    0,
  );

  const totalTicks = Math.floor(minutes * 60 * 60);
  const metrics = {
    challengerBallHalfTicks: 0,
    championBallHalfTicks: 0,
    neutralBallTicks: 0,
    challengerAttackThirdTicks: 0,
    championAttackThirdTicks: 0,
    sampledTicks: 0,
  };

  function runPolicy(
    policy,
    bots,
    teamId,
    state,
    gameState,
    errorSink,
  ) {
    const players = statePlayers(state);
    for (const bot of bots) {
      const player = state.getPlayer?.(bot.id) ||
        players.find((candidate) => Number(candidate.id) === bot.id);
      if (!player || !discOf(player)?.pos) continue;

      try {
        const features = buildFeatureObject(player, state, gameState);
        if (!features) continue;
        const canonical = policy.act({
          agent_id: String(bot.id),
          role: bot.role,
          features,
        });
        const action = canonicalActionToWorld(canonical, teamId);
        room.playerInput(
          Utils.keyState(action.dirX, action.dirY, action.kick),
          bot.id,
        );
        bot.actions += 1;
        if (action.kick) bot.kicks += 1;
        bot.xSum += canonicalPosition(player, teamId);
        bot.xSamples += 1;
      } catch (error) {
        errorSink(error);
      }
    }
  }

  for (let tick = 0; tick < totalTicks; tick += 1) {
    if (tick % sampleEvery === 0) {
      const state = room.state;
      const gameState = room.gameState;
      const ball = gameState?.physicsState?.discs?.[0];
      if (state && gameState && ball?.pos) {
        runPolicy(
          challenger,
          challengerBots,
          challengerTeamId,
          state,
          gameState,
          () => { challengerErrors += 1; },
        );
        runPolicy(
          champion,
          championBots,
          championTeamId,
          state,
          gameState,
          () => { championErrors += 1; },
        );

        const challengerAxisX =
          (challengerTeamId === 1 ? 1 : -1) * num(ball.pos.x);
        metrics.sampledTicks += 1;
        if (challengerAxisX > 10) metrics.challengerBallHalfTicks += 1;
        else if (challengerAxisX < -10) metrics.championBallHalfTicks += 1;
        else metrics.neutralBallTicks += 1;

        const attackThird =
          Math.max(110, Number(stadium.width || 800) * 0.34);
        if (challengerAxisX > attackThird) {
          metrics.challengerAttackThirdTicks += 1;
        } else if (challengerAxisX < -attackThird) {
          metrics.championAttackThirdTicks += 1;
        }
      }
    }
    room.runSteps(1);
  }

  const finalScore = scoreFromGameState(room.gameState);
  if (goalEvents === 0 && (finalScore.red > 0 || finalScore.blue > 0)) {
    redGoals = finalScore.red;
    blueGoals = finalScore.blue;
  }

  const challengerGoals =
    challengerTeamId === 1 ? redGoals : blueGoals;
  const championGoals =
    challengerTeamId === 1 ? blueGoals : redGoals;
  const sampled = Math.max(1, metrics.sampledTicks);

  const result = {
    match_index: matchIndex,
    seed,
    start_state: {
      ball_x: startX,
      ball_y: startY,
      ball_vx: startVx,
      ball_vy: startVy,
    },
    challenger_team_id: challengerTeamId,
    champion_team_id: championTeamId,
    goals: {
      challenger: challengerGoals,
      champion: championGoals,
      red: redGoals,
      blue: blueGoals,
    },
    result:
      challengerGoals > championGoals ? "win" :
      challengerGoals < championGoals ? "loss" : "draw",
    territory: {
      challenger_half_rate: metrics.challengerBallHalfTicks / sampled,
      champion_half_rate: metrics.championBallHalfTicks / sampled,
      neutral_rate: metrics.neutralBallTicks / sampled,
      challenger_attack_third_rate:
        metrics.challengerAttackThirdTicks / sampled,
      champion_attack_third_rate:
        metrics.championAttackThirdTicks / sampled,
    },
    challenger: {
      runtime_errors: challengerErrors,
      total_actions: challengerBots.reduce((sum, bot) => sum + bot.actions, 0),
      total_kicks: challengerBots.reduce((sum, bot) => sum + bot.kicks, 0),
      roles: Object.fromEntries(
        challengerBots.map((bot) => [
          bot.role,
          {
            actions: bot.actions,
            kicks: bot.kicks,
            average_canonical_x:
              bot.xSum / Math.max(1, bot.xSamples),
          },
        ]),
      ),
    },
    champion: {
      runtime_errors: championErrors,
      total_actions: championBots.reduce((sum, bot) => sum + bot.actions, 0),
      total_kicks: championBots.reduce((sum, bot) => sum + bot.kicks, 0),
      roles: Object.fromEntries(
        championBots.map((bot) => [
          bot.role,
          {
            actions: bot.actions,
            kicks: bot.kicks,
            average_canonical_x:
              bot.xSum / Math.max(1, bot.xSamples),
          },
        ]),
      ),
    },
  };

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}
  return result;
}

function summarize(matches, challengerPath, championPath, stadium) {
  const wins = matches.filter((row) => row.result === "win").length;
  const losses = matches.filter((row) => row.result === "loss").length;
  const draws = matches.length - wins - losses;
  const challengerGoals = matches.reduce(
    (sum, row) => sum + row.goals.challenger,
    0,
  );
  const championGoals = matches.reduce(
    (sum, row) => sum + row.goals.champion,
    0,
  );
  const avg = (selector) =>
    matches.reduce((sum, row) => sum + selector(row), 0) /
    Math.max(1, matches.length);

  const bySide = {};
  for (const teamId of [1, 2]) {
    const rows = matches.filter(
      (row) => row.challenger_team_id === teamId,
    );
    if (!rows.length) continue;
    bySide[String(teamId)] = {
      matches: rows.length,
      wins: rows.filter((row) => row.result === "win").length,
      draws: rows.filter((row) => row.result === "draw").length,
      losses: rows.filter((row) => row.result === "loss").length,
      challenger_half_rate:
        rows.reduce(
          (sum, row) => sum + row.territory.challenger_half_rate,
          0,
        ) / rows.length,
      challenger_attack_third_rate:
        rows.reduce(
          (sum, row) => sum + row.territory.challenger_attack_third_rate,
          0,
        ) / rows.length,
    };
  }

  const challengerActions = matches.reduce(
    (sum, row) => sum + row.challenger.total_actions,
    0,
  );
  const challengerKicks = matches.reduce(
    (sum, row) => sum + row.challenger.total_kicks,
    0,
  );

  return {
    schema: "haxlab-elite-model-duel-v1",
    challenger_model: challengerPath,
    champion_model: championPath,
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
      challenger: challengerGoals,
      champion: championGoals,
      differential: challengerGoals - championGoals,
    },
    territory: {
      challenger_half_rate: avg(
        (row) => row.territory.challenger_half_rate,
      ),
      champion_half_rate: avg(
        (row) => row.territory.champion_half_rate,
      ),
      challenger_attack_third_rate: avg(
        (row) => row.territory.challenger_attack_third_rate,
      ),
      champion_attack_third_rate: avg(
        (row) => row.territory.champion_attack_third_rate,
      ),
    },
    challenger_runtime_errors: matches.reduce(
      (sum, row) => sum + row.challenger.runtime_errors,
      0,
    ),
    champion_runtime_errors: matches.reduce(
      (sum, row) => sum + row.champion.runtime_errors,
      0,
    ),
    challenger_kick_action_rate:
      challengerKicks / Math.max(1, challengerActions),
    by_challenger_side: bySide,
    match_results: matches,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const challengerModel = JSON.parse(
    fs.readFileSync(args.challenger, "utf8"),
  );
  const championModel = JSON.parse(
    fs.readFileSync(args.champion, "utf8"),
  );
  const stadiumJson = JSON.parse(fs.readFileSync(args.stadium, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));

  const matches = [];
  for (let index = 0; index < args.matches; index += 1) {
    const challengerTeamId = index % 2 === 0 ? 1 : 2;
    const pairSeed = args.seed + Math.floor(index / 2);
    const result = runMatch({
      challengerModel,
      championModel,
      stadium,
      challengerTeamId,
      minutes: args.minutes,
      sampleEvery: args.sampleEvery,
      matchIndex: index + 1,
      seed: pairSeed,
    });
    matches.push(result);
    console.error(
      "[" + (index + 1) + "/" + args.matches + "] side=" +
      challengerTeamId + " seed=" + pairSeed + " " +
      result.goals.challenger + "-" + result.goals.champion + " " +
      result.result,
    );
  }

  const summary = summarize(
    matches,
    args.challenger,
    args.champion,
    stadium,
  );
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
  makeRng,
  runMatch,
  summarize,
};
