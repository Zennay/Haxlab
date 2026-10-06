#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
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
const {
  enforceKickRange,
  recoveryAction,
} = require("./elite_tactics");
const {
  prepareReplayScenario,
} = require("./sandbox_replay_start");

const ROLES = ["gk", "dm", "am", "st"];
const ARENA_SCHEMA = "haxlab-closed-loop-arena-v2";

function usage() {
  console.error(
    "Usage: node tools/elite_closed_loop_arena_v2.js " +
      "--challenger challenger.json --champion champion.json " +
      "--stadium stadium.hbs --scenarios scenarios.json " +
      "[--partner-model runtime-model.json ...] [--seconds 30] " +
      "[--max-scenarios 4] [--sample-every 6] [--plug-repeats 1] " +
      "[--seed 1337] [--output result.json]",
  );
  process.exit(2);
}

function requireOptionValue(key, value) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.startsWith("--")
  ) {
    throw new Error("Missing value for " + key);
  }
  return value;
}

function parseFiniteNumberOption(
  key,
  value,
  { minimum = -Infinity, maximum = Infinity } = {},
) {
  const raw = requireOptionValue(key, value);
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) {
    throw new Error("Invalid numeric value for " + key + ": " + raw);
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error("Out-of-range value for " + key + ": " + raw);
  }
  return parsed;
}

function parseIntegerOption(
  key,
  value,
  { minimum = Number.MIN_SAFE_INTEGER, maximum = Number.MAX_SAFE_INTEGER } = {},
) {
  const raw = requireOptionValue(key, value);
  if (!/^[+-]?\d+$/.test(raw)) {
    throw new Error("Invalid integer value for " + key + ": " + raw);
  }
  const parsed = Number(raw);
  if (
    !Number.isSafeInteger(parsed) ||
    parsed < minimum ||
    parsed > maximum
  ) {
    throw new Error("Out-of-range integer for " + key + ": " + raw);
  }
  return parsed;
}

function parseArgs(argv) {
  const args = {
    partnerModels: [],
    seconds: 30,
    maxScenarios: 4,
    sampleEvery: 6,
    plugRepeats: 1,
    seed: 1337,
    output: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--challenger") {
      args.challenger = requireOptionValue(key, value);
      i += 1;
    }
    else if (key === "--champion") {
      args.champion = requireOptionValue(key, value);
      i += 1;
    }
    else if (key === "--stadium") {
      args.stadium = requireOptionValue(key, value);
      i += 1;
    }
    else if (key === "--scenarios") {
      args.scenarios = requireOptionValue(key, value);
      i += 1;
    }
    else if (key === "--partner-model") {
      args.partnerModels.push(requireOptionValue(key, value));
      i += 1;
    }
    else if (key === "--seconds") {
      args.seconds = parseFiniteNumberOption(
        key,
        value,
        { minimum: 5 },
      );
      i += 1;
    }
    else if (key === "--max-scenarios") {
      args.maxScenarios = parseIntegerOption(
        key,
        value,
        { minimum: 1 },
      );
      i += 1;
    }
    else if (key === "--sample-every") {
      args.sampleEvery = parseIntegerOption(
        key,
        value,
        { minimum: 1 },
      );
      i += 1;
    }
    else if (key === "--plug-repeats") {
      args.plugRepeats = parseIntegerOption(
        key,
        value,
        { minimum: 1 },
      );
      i += 1;
    }
    else if (key === "--seed") {
      args.seed = parseIntegerOption(
        key,
        value,
        { minimum: 1, maximum: 0xffffffff },
      );
      i += 1;
    }
    else if (key === "--output") {
      args.output = requireOptionValue(key, value);
      i += 1;
    }
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("Unknown argument: " + key);
  }
  if (!args.challenger || !args.champion || !args.stadium || !args.scenarios) {
    usage();
  }
  if (!args.partnerModels.length) args.partnerModels = [args.champion];
  return args;
}

function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function makeRng(seed) {
  let state = (seed >>> 0) || 1;
  return function rng() {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function actionKey(action) {
  return [
    Number(action?.dirX || 0),
    Number(action?.dirY || 0),
    Boolean(action?.kick) ? 1 : 0,
  ].join(":");
}

function angleDelta(a, b) {
  let delta = Math.abs(Number(a || 0) - Number(b || 0));
  while (delta > Math.PI) delta = Math.abs(delta - 2 * Math.PI);
  return delta;
}

function contextShifted(anchor, current, {
  minAngle = 0.45,
  minDistanceDelta = 80,
} = {}) {
  if (!anchor || !current) return false;
  return (
    angleDelta(anchor.angle, current.angle) >= minAngle ||
    Math.abs(Number(anchor.distance) - Number(current.distance)) >= minDistanceDelta
  );
}

function choosePartnerModel(pool, seed, scenarioIndex, roleIndex, repeatIndex) {
  if (!Array.isArray(pool) || pool.length === 0) {
    throw new Error("partner model pool may not be empty");
  }
  const rng = makeRng(
    Number(seed) +
    Number(scenarioIndex) * 1009 +
    Number(roleIndex) * 101 +
    Number(repeatIndex) * 17,
  );
  return pool[Math.floor(rng() * pool.length) % pool.length];
}

function createTracker(bot) {
  return {
    role: bot.role,
    model_kind: bot.modelKind,
    model_path: bot.modelPath,
    policy_samples: 0,
    kicks: 0,
    near_ball_samples: 0,
    close_ball_samples: 0,
    ball_distance_sum: 0,
    role_deviation_sum: 0,
    role_deviation_samples: 0,
    boundary_samples: 0,
    ood_samples: 0,
    ood_max_abs_z: 0,
    far_stall_samples: 0,
    current_far_stall_samples: 0,
    max_far_stall_samples: 0,
    current_action_run_samples: 0,
    max_action_run_samples: 0,
    last_action_key: null,
    action_anchor_context: null,
    context_shift_accounted: false,
    context_adaptations: 0,
    context_misses: 0,
    runtime_errors: 0,
  };
}

function currentContext(player, gameState) {
  const p = discOf(player);
  const ball = gameState?.physicsState?.discs?.[0];
  if (!p?.pos || !ball?.pos) return null;
  const dx = num(ball.pos.x) - num(p.pos.x);
  const dy = num(ball.pos.y) - num(p.pos.y);
  return {
    dx,
    dy,
    distance: Math.hypot(dx, dy),
    angle: Math.atan2(dy, dx),
  };
}

function updateTracker(
  tracker,
  {
    action,
    canonical,
    player,
    gameState,
    teamId,
    stadium,
    sampleEvery,
  },
) {
  const context = currentContext(player, gameState);
  const playerDisc = discOf(player);
  if (!context || !playerDisc?.pos) return;

  tracker.policy_samples += 1;
  tracker.ball_distance_sum += context.distance;
  if (context.distance <= 150) tracker.near_ball_samples += 1;
  if (context.distance <= 60) tracker.close_ball_samples += 1;
  if (action.kick) tracker.kicks += 1;

  const ood = Number(canonical?.ood_max_abs_z || 0);
  tracker.ood_max_abs_z = Math.max(tracker.ood_max_abs_z, ood);
  if (ood > 8) tracker.ood_samples += 1;

  const width = Number(stadium.width || 800);
  const height = Number(stadium.height || 350);
  if (
    Math.abs(num(playerDisc.pos.x)) >= width * 0.94 ||
    Math.abs(num(playerDisc.pos.y)) >= height * 0.90
  ) {
    tracker.boundary_samples += 1;
  }

  const recovery = recoveryAction(
    tracker.role,
    player,
    gameState,
    teamId,
    { stadiumWidth: width },
  );
  if (recovery) {
    tracker.role_deviation_sum += Math.hypot(
      num(recovery.target_canonical_x) - num(recovery.canonical_player_x),
      num(recovery.target_y) - num(playerDisc.pos.y),
    );
    tracker.role_deviation_samples += 1;
  }

  const stationary =
    Number(action.dirX || 0) === 0 &&
    Number(action.dirY || 0) === 0;
  if (stationary && context.distance >= 180) {
    tracker.far_stall_samples += 1;
    tracker.current_far_stall_samples += 1;
    tracker.max_far_stall_samples = Math.max(
      tracker.max_far_stall_samples,
      tracker.current_far_stall_samples,
    );
  } else {
    tracker.current_far_stall_samples = 0;
  }

  const key = actionKey(action);
  if (tracker.last_action_key == null) {
    tracker.last_action_key = key;
    tracker.current_action_run_samples = 1;
    tracker.max_action_run_samples = 1;
    tracker.action_anchor_context = context;
    tracker.context_shift_accounted = false;
    return;
  }

  const shifted = contextShifted(tracker.action_anchor_context, context);
  if (key !== tracker.last_action_key) {
    if (shifted) tracker.context_adaptations += 1;
    tracker.last_action_key = key;
    tracker.current_action_run_samples = 1;
    tracker.max_action_run_samples = Math.max(
      tracker.max_action_run_samples,
      1,
    );
    tracker.action_anchor_context = context;
    tracker.context_shift_accounted = false;
  } else {
    tracker.current_action_run_samples += 1;
    tracker.max_action_run_samples = Math.max(
      tracker.max_action_run_samples,
      tracker.current_action_run_samples,
    );
    if (
      shifted &&
      !tracker.context_shift_accounted &&
      tracker.current_action_run_samples >= 3
    ) {
      tracker.context_misses += 1;
      tracker.context_shift_accounted = true;
    }
  }

  tracker.sample_every_ticks = sampleEvery;
}

function finishTracker(tracker, sampleEvery) {
  const samples = Math.max(1, tracker.policy_samples);
  const adaptationTotal =
    tracker.context_adaptations + tracker.context_misses;
  return {
    role: tracker.role,
    model_kind: tracker.model_kind,
    model_path: tracker.model_path,
    policy_samples: tracker.policy_samples,
    kicks: tracker.kicks,
    near_ball_rate: tracker.near_ball_samples / samples,
    close_ball_rate: tracker.close_ball_samples / samples,
    average_ball_distance: tracker.ball_distance_sum / samples,
    average_role_deviation:
      tracker.role_deviation_sum /
      Math.max(1, tracker.role_deviation_samples),
    boundary_rate: tracker.boundary_samples / samples,
    ood_rate: tracker.ood_samples / samples,
    ood_max_abs_z: tracker.ood_max_abs_z,
    far_stall_rate: tracker.far_stall_samples / samples,
    max_far_stall_seconds:
      tracker.max_far_stall_samples * sampleEvery / 60,
    max_held_action_seconds:
      tracker.max_action_run_samples * sampleEvery / 60,
    context_adaptations: tracker.context_adaptations,
    context_misses: tracker.context_misses,
    context_adaptation_rate:
      adaptationTotal > 0
        ? tracker.context_adaptations / adaptationTotal
        : 1.0,
    runtime_errors: tracker.runtime_errors,
  };
}

function addPlayer(room, id, name, teamId) {
  room.playerJoin(
    id,
    name,
    "xx",
    "AI",
    "arena-v2-" + id,
    "arena-v2-" + id,
  );
  room.setPlayerTeam(id, teamId, 0);
}

function canonicalPosition(player, teamId) {
  const disc = discOf(player);
  if (!disc?.pos) return null;
  return (Number(teamId) === 1 ? 1 : -1) * num(disc.pos.x);
}

function formationSnapshot(bots, players, teamId) {
  const positions = {};
  for (const bot of bots) {
    const player = players.find(
      (candidate) => Number(candidate.id) === Number(bot.id),
    );
    const x = player ? canonicalPosition(player, teamId) : null;
    if (x == null) return null;
    positions[bot.role] = x;
  }
  const ordered =
    positions.gk <= positions.dm &&
    positions.dm <= positions.am &&
    positions.am <= positions.st;
  const gaps = [
    positions.dm - positions.gk,
    positions.am - positions.dm,
    positions.st - positions.am,
  ];
  const span = positions.st - positions.gk;
  return {
    ordered,
    span,
    collapsed: gaps.some((gap) => gap < 28) || span < 150,
    overstretched: span > 680,
  };
}

function nearestTeamToBall(players, ball) {
  if (!ball?.pos) return 0;
  let bestTeam = 0;
  let bestDistance2 = Infinity;
  for (const player of players) {
    const disc = discOf(player);
    const teamId = Number(player.team?.id || player.teamId || 0);
    if (!(teamId === 1 || teamId === 2) || !disc?.pos) continue;
    const dx = num(disc.pos.x) - num(ball.pos.x);
    const dy = num(disc.pos.y) - num(ball.pos.y);
    const distance2 = dx * dx + dy * dy;
    if (distance2 < bestDistance2) {
      bestDistance2 = distance2;
      bestTeam = teamId;
    }
  }
  return bestTeam;
}

function scenarioIndexForRow(scenario, scenarioOffset) {
  if (!scenario || typeof scenario !== "object" || Array.isArray(scenario)) {
    throw new Error(
      "scenario " + (scenarioOffset + 1) + " must be an object",
    );
  }
  const declared = scenario.scenario_index;
  if (declared === undefined || declared === null) {
    return scenarioOffset + 1;
  }
  if (!Number.isSafeInteger(declared) || declared < 1) {
    throw new Error(
      "scenario " + (scenarioOffset + 1) +
      " has invalid scenario_index: " + String(declared),
    );
  }
  return declared;
}

function loadScenarioRows(filePath, maxScenarios) {
  if (!Number.isSafeInteger(maxScenarios) || maxScenarios < 1) {
    throw new Error("maxScenarios must be a positive safe integer");
  }
  const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
  const rows = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object" &&
        !Array.isArray(payload) &&
        Array.isArray(payload.scenarios)
      ? payload.scenarios
      : [];
  if (!rows.length) throw new Error("scenario file contains no scenarios");

  const selected = rows.slice(0, maxScenarios);
  const seenScenarioIndices = new Set();
  for (let offset = 0; offset < selected.length; offset += 1) {
    const scenarioIndex = scenarioIndexForRow(selected[offset], offset);
    if (seenScenarioIndices.has(scenarioIndex)) {
      throw new Error("duplicate scenario_index: " + scenarioIndex);
    }
    seenScenarioIndices.add(scenarioIndex);
  }
  return selected;
}

function createRuntimeMap(modelPaths) {
  const result = new Map();
  for (const modelPath of new Set(modelPaths)) {
    const model = JSON.parse(fs.readFileSync(modelPath, "utf8"));
    result.set(modelPath, new ElitePolicyRuntime(model));
  }
  return result;
}

function primeRuntimes(runtimeMap, bots, scenario) {
  for (const runtime of runtimeMap.values()) runtime.reset();
  const history = Array.isArray(scenario.history) ? scenario.history : [];
  for (const frame of history) {
    for (const bot of bots) {
      const features =
        frame.features?.[String(bot.sourceTeamId)]?.[bot.role];
      if (!features) {
        throw new Error(
          "scenario history missing source role " +
          bot.sourceTeamId + "/" + bot.role,
        );
      }
      runtimeMap.get(bot.modelPath).act({
        agent_id: String(bot.id),
        role: bot.role,
        features,
      });
    }
  }
}

function buildLineup({
  mode,
  testedRole,
  testTeamId,
  testedPath,
  testedKind,
  championPath,
  partnerPool,
  seed,
  scenarioIndex,
  repeatIndex,
}) {
  const opponentTeamId = testTeamId === 1 ? 2 : 1;
  const testBots = [];
  const opponentBots = [];
  for (let index = 0; index < ROLES.length; index += 1) {
    const role = ROLES[index];
    let modelPath = testedPath;
    let modelKind = testedKind;
    if (mode === "plug_and_play" && role !== testedRole) {
      modelPath = choosePartnerModel(
        partnerPool,
        seed,
        scenarioIndex,
        index,
        repeatIndex,
      );
      modelKind = "partner";
    }
    testBots.push({
      id: 100 + index,
      role,
      teamId: testTeamId,
      sourceTeamId: 1,
      isElite: true,
      modelPath,
      modelKind,
      tracker: null,
    });
    opponentBots.push({
      id: 200 + index,
      role,
      teamId: opponentTeamId,
      sourceTeamId: 2,
      isElite: false,
      modelPath: championPath,
      modelKind: "champion",
      tracker: null,
    });
  }
  return { testBots, opponentBots };
}

function proxyScore(progressionShare, halfRate, attackThirdRate) {
  return (
    0.50 * Number(progressionShare || 0) +
    0.30 * Number(halfRate || 0) +
    0.20 * Number(attackThirdRate || 0)
  );
}

function runArenaMatch({
  mode,
  testedRole,
  testedPath,
  testedKind,
  championPath,
  partnerPool,
  stadium,
  scenario,
  scenarioIndex,
  testTeamId,
  seconds,
  sampleEvery,
  seed,
  repeatIndex,
}) {
  const { testBots, opponentBots } = buildLineup({
    mode,
    testedRole,
    testTeamId,
    testedPath,
    testedKind,
    championPath,
    partnerPool,
    seed,
    scenarioIndex,
    repeatIndex,
  });
  const allBots = [...testBots, ...opponentBots];
  const runtimeMap = createRuntimeMap(allBots.map((bot) => bot.modelPath));
  for (const bot of allBots) bot.tracker = createTracker(bot);

  let redGoals = 0;
  let blueGoals = 0;
  const room = Room.sandbox(
    {
      onTeamGoal: (teamId) => {
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

  for (const bot of allBots) {
    addPlayer(
      room,
      bot.id,
      (bot.modelKind === "challenger" ? "Test" : bot.modelKind) +
        "-" + bot.role.toUpperCase(),
      bot.teamId,
    );
  }

  room.startGame(0);
  room.runSteps(5);
  prepareReplayScenario(room, allBots, scenario, testTeamId);
  primeRuntimes(runtimeMap, allBots, scenario);

  const sampled = {
    ticks: 0,
    test_half: 0,
    opponent_half: 0,
    neutral: 0,
    test_attack_third: 0,
    opponent_attack_third: 0,
    test_possession: 0,
    opponent_possession: 0,
    formation_samples: 0,
    formation_ordered: 0,
    formation_collapsed: 0,
    formation_overstretched: 0,
    test_progress: 0,
    opponent_progress: 0,
  };

  let previousAxisX = null;
  const totalTicks = Math.floor(seconds * 60);

  for (let tick = 0; tick < totalTicks; tick += 1) {
    if (tick % sampleEvery === 0) {
      const state = room.state;
      const gameState = room.gameState;
      const ball = gameState?.physicsState?.discs?.[0];
      if (state && gameState && ball?.pos) {
        const players = statePlayers(state);
        for (const bot of allBots) {
          const player =
            state.getPlayer?.(bot.id) ||
            players.find(
              (candidate) => Number(candidate.id) === Number(bot.id),
            );
          if (!player || !discOf(player)?.pos) continue;
          try {
            const features = buildFeatureObject(player, state, gameState);
            if (!features) continue;
            const ownGoals = bot.teamId === 1 ? redGoals : blueGoals;
            const otherGoals = bot.teamId === 1 ? blueGoals : redGoals;
            features.score_diff = ownGoals - otherGoals;

            const runtime = runtimeMap.get(bot.modelPath);
            const canonical = runtime.act({
              agent_id: String(bot.id),
              role: bot.role,
              features,
            });
            let action = canonicalActionToWorld(canonical, bot.teamId);
            action = enforceKickRange(action, player, gameState);
            updateTracker(bot.tracker, {
              action,
              canonical,
              player,
              gameState,
              teamId: bot.teamId,
              stadium,
              sampleEvery,
            });
            room.playerInput(
              Utils.keyState(action.dirX, action.dirY, action.kick),
              bot.id,
            );
          } catch (_) {
            bot.tracker.runtime_errors += 1;
          }
        }

        sampled.ticks += 1;
        const nearest = nearestTeamToBall(players, ball);
        const opponentTeamId = testTeamId === 1 ? 2 : 1;
        if (nearest === testTeamId) sampled.test_possession += 1;
        else if (nearest === opponentTeamId) sampled.opponent_possession += 1;

        const testAxisX =
          (testTeamId === 1 ? 1 : -1) * num(ball.pos.x);
        if (testAxisX > 10) sampled.test_half += 1;
        else if (testAxisX < -10) sampled.opponent_half += 1;
        else sampled.neutral += 1;

        const attackThird =
          Math.max(110, Number(stadium.width || 800) * 0.34);
        if (testAxisX > attackThird) sampled.test_attack_third += 1;
        else if (testAxisX < -attackThird) {
          sampled.opponent_attack_third += 1;
        }

        if (previousAxisX != null) {
          const delta = testAxisX - previousAxisX;
          if (delta > 0) sampled.test_progress += delta;
          else if (delta < 0) sampled.opponent_progress += -delta;
        }
        previousAxisX = testAxisX;

        const formation = formationSnapshot(testBots, players, testTeamId);
        if (formation) {
          sampled.formation_samples += 1;
          if (formation.ordered) sampled.formation_ordered += 1;
          if (formation.collapsed) sampled.formation_collapsed += 1;
          if (formation.overstretched) sampled.formation_overstretched += 1;
        }
      }
    }
    room.runSteps(1);
  }

  const samples = Math.max(1, sampled.ticks);
  const progressionTotal =
    sampled.test_progress + sampled.opponent_progress;
  const testProgression =
    progressionTotal > 0 ? sampled.test_progress / progressionTotal : 0.5;
  const opponentProgression = 1 - testProgression;
  const testHalf = sampled.test_half / samples;
  const opponentHalf = sampled.opponent_half / samples;
  const testAttack = sampled.test_attack_third / samples;
  const opponentAttack = sampled.opponent_attack_third / samples;
  const testProxy = proxyScore(testProgression, testHalf, testAttack);
  const opponentProxy = proxyScore(
    opponentProgression,
    opponentHalf,
    opponentAttack,
  );

  const testGoals = testTeamId === 1 ? redGoals : blueGoals;
  const opponentGoals = testTeamId === 1 ? blueGoals : redGoals;
  const testRoleRows = Object.fromEntries(
    testBots.map((bot) => [
      bot.role,
      finishTracker(bot.tracker, sampleEvery),
    ]),
  );

  const result = {
    mode,
    tested_kind: testedKind,
    tested_role: testedRole || null,
    scenario_index: scenarioIndex,
    test_team_id: testTeamId,
    repeat_index: repeatIndex,
    lineup: Object.fromEntries(
      testBots.map((bot) => [
        bot.role,
        {
          model_kind: bot.modelKind,
          model_path: bot.modelPath,
        },
      ]),
    ),
    goals: {
      test: testGoals,
      opponent: opponentGoals,
      differential: testGoals - opponentGoals,
    },
    proxy: {
      test_score: testProxy,
      opponent_score: opponentProxy,
    },
    territory: {
      test_half_rate: testHalf,
      opponent_half_rate: opponentHalf,
      neutral_rate: sampled.neutral / samples,
      test_attack_third_rate: testAttack,
      opponent_attack_third_rate: opponentAttack,
    },
    progression: {
      test_share: testProgression,
      opponent_share: opponentProgression,
    },
    possession_proxy: {
      test_rate:
        sampled.test_possession /
        Math.max(1, sampled.test_possession + sampled.opponent_possession),
    },
    team_shape: {
      formation_order_rate:
        sampled.formation_ordered /
        Math.max(1, sampled.formation_samples),
      collapsed_rate:
        sampled.formation_collapsed /
        Math.max(1, sampled.formation_samples),
      overstretched_rate:
        sampled.formation_overstretched /
        Math.max(1, sampled.formation_samples),
    },
    test_team: {
      roles: testRoleRows,
      runtime_errors: Object.values(testRoleRows).reduce(
        (sum, row) => sum + Number(row.runtime_errors || 0),
        0,
      ),
    },
  };

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}
  return result;
}

function requireArenaMetric(
  value,
  label,
  { minimum = -Infinity, maximum = Infinity, integer = false } = {},
) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(label + " must be a finite number");
  }
  if (integer && !Number.isSafeInteger(value)) {
    throw new Error(label + " must be a safe integer");
  }
  if (value < minimum || value > maximum) {
    throw new Error(label + " is out of range");
  }
  return value;
}

function requireArenaObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(label + " must be an object");
  }
  return value;
}

function requireArenaLineup(row, expectedKind, label) {
  const lineup = requireArenaObject(row.lineup, label + " lineup");
  for (const role of ROLES) {
    const entry = requireArenaObject(
      lineup[role],
      label + " lineup." + role,
    );
    if (
      typeof entry.model_path !== "string" ||
      entry.model_path.trim().length === 0
    ) {
      throw new Error(label + " lineup." + role + " model_path is invalid");
    }
    const expectedRoleKind =
      row.mode === "plug_and_play" && role !== row.tested_role
        ? "partner"
        : expectedKind;
    if (entry.model_kind !== expectedRoleKind) {
      throw new Error(
        label + " lineup." + role + " model_kind must be " +
        expectedRoleKind,
      );
    }
  }
  return lineup;
}

function requireArenaPairRow(row, expectedKind, label) {
  requireArenaObject(row, label + " arena row");
  if (row.tested_kind !== expectedKind) {
    throw new Error(
      label + " tested_kind must be " + expectedKind +
      ", got " + String(row.tested_kind),
    );
  }
  if (!(row.mode === "full_team" || row.mode === "plug_and_play")) {
    throw new Error(label + " has invalid arena mode: " + String(row.mode));
  }
  if (row.mode === "full_team" && row.tested_role !== null) {
    throw new Error(label + " full_team row must have tested_role=null");
  }
  if (
    row.mode === "plug_and_play" &&
    !ROLES.includes(row.tested_role)
  ) {
    throw new Error(
      label + " plug_and_play row has invalid tested_role: " +
      String(row.tested_role),
    );
  }
  if (!Number.isSafeInteger(row.scenario_index) || row.scenario_index < 1) {
    throw new Error(label + " has invalid scenario_index");
  }
  if (!(row.test_team_id === 1 || row.test_team_id === 2)) {
    throw new Error(label + " has invalid test_team_id");
  }
  if (!Number.isSafeInteger(row.repeat_index) || row.repeat_index < 0) {
    throw new Error(label + " has invalid repeat_index");
  }

  requireArenaLineup(row, expectedKind, label);

  const proxy = requireArenaObject(row.proxy, label + " proxy");
  requireArenaMetric(
    proxy.test_score,
    label + " proxy.test_score",
    { minimum: 0, maximum: 1 },
  );

  const goals = requireArenaObject(row.goals, label + " goals");
  requireArenaMetric(
    goals.differential,
    label + " goals.differential",
    { integer: true },
  );

  const progression = requireArenaObject(
    row.progression,
    label + " progression",
  );
  requireArenaMetric(
    progression.test_share,
    label + " progression.test_share",
    { minimum: 0, maximum: 1 },
  );

  const possession = requireArenaObject(
    row.possession_proxy,
    label + " possession_proxy",
  );
  requireArenaMetric(
    possession.test_rate,
    label + " possession_proxy.test_rate",
    { minimum: 0, maximum: 1 },
  );

  const shape = requireArenaObject(row.team_shape, label + " team_shape");
  for (const key of [
    "formation_order_rate",
    "collapsed_rate",
    "overstretched_rate",
  ]) {
    requireArenaMetric(
      shape[key],
      label + " team_shape." + key,
      { minimum: 0, maximum: 1 },
    );
  }

  const team = requireArenaObject(row.test_team, label + " test_team");
  const roles = requireArenaObject(team.roles, label + " test_team.roles");
  for (const role of ROLES) {
    const metrics = requireArenaObject(
      roles[role],
      label + " test_team.roles." + role,
    );
    for (const key of [
      "near_ball_rate",
      "boundary_rate",
      "ood_rate",
      "far_stall_rate",
      "context_adaptation_rate",
    ]) {
      requireArenaMetric(
        metrics[key],
        label + " " + role + "." + key,
        { minimum: 0, maximum: 1 },
      );
    }
    for (const key of [
      "average_ball_distance",
      "average_role_deviation",
      "max_held_action_seconds",
    ]) {
      requireArenaMetric(
        metrics[key],
        label + " " + role + "." + key,
        { minimum: 0 },
      );
    }
    requireArenaMetric(
      metrics.runtime_errors,
      label + " " + role + ".runtime_errors",
      { minimum: 0, integer: true },
    );
  }
}

function pairArenaRows(candidate, reference, tieMargin = 0.025) {
  requireArenaPairRow(candidate, "challenger", "candidate");
  requireArenaPairRow(reference, "reference", "reference");
  if (
    typeof tieMargin !== "number" ||
    !Number.isFinite(tieMargin) ||
    tieMargin < 0 ||
    tieMargin > 1
  ) {
    throw new Error("arena tie margin must be a finite rate");
  }
  if (
    candidate.mode !== reference.mode ||
    candidate.tested_role !== reference.tested_role ||
    candidate.scenario_index !== reference.scenario_index ||
    candidate.test_team_id !== reference.test_team_id ||
    candidate.repeat_index !== reference.repeat_index
  ) {
    throw new Error("arena candidate/reference row mismatch");
  }
  if (candidate.mode === "plug_and_play") {
    for (const role of ROLES) {
      if (role === candidate.tested_role) continue;
      if (
        candidate.lineup[role].model_path !==
        reference.lineup[role].model_path
      ) {
        throw new Error(
          "arena paired partner lineup mismatch for role " + role,
        );
      }
    }
  }
  const delta =
    candidate.proxy.test_score -
    reference.proxy.test_score;
  return {
    mode: candidate.mode,
    tested_role: candidate.tested_role,
    scenario_index: candidate.scenario_index,
    test_team_id: candidate.test_team_id,
    repeat_index: candidate.repeat_index,
    proxy_delta: delta,
    result:
      delta > tieMargin ? "win" :
      delta < -tieMargin ? "loss" : "draw",
    candidate,
    reference,
  };
}

function average(rows, selector) {
  if (!rows.length) return 0;
  return rows.reduce((sum, row) => sum + Number(selector(row) || 0), 0) /
    rows.length;
}

function summarizePairs(pairs) {
  const wins = pairs.filter((row) => row.result === "win").length;
  const draws = pairs.filter((row) => row.result === "draw").length;
  const losses = pairs.length - wins - draws;
  return {
    matches: pairs.length,
    wins,
    draws,
    losses,
    proxy_match_score:
      (wins + 0.5 * draws) / Math.max(1, pairs.length),
    average_proxy_delta: average(pairs, (row) => row.proxy_delta),
    candidate: {
      proxy_score: average(
        pairs,
        (row) => row.candidate.proxy.test_score,
      ),
      goal_differential: pairs.reduce(
        (sum, row) => sum + Number(row.candidate.goals.differential || 0),
        0,
      ),
      progression_share: average(
        pairs,
        (row) => row.candidate.progression.test_share,
      ),
      possession_proxy_rate: average(
        pairs,
        (row) => row.candidate.possession_proxy.test_rate,
      ),
      formation_order_rate: average(
        pairs,
        (row) => row.candidate.team_shape.formation_order_rate,
      ),
      collapsed_shape_rate: average(
        pairs,
        (row) => row.candidate.team_shape.collapsed_rate,
      ),
      overstretched_shape_rate: average(
        pairs,
        (row) => row.candidate.team_shape.overstretched_rate,
      ),
    },
    reference: {
      proxy_score: average(
        pairs,
        (row) => row.reference.proxy.test_score,
      ),
      goal_differential: pairs.reduce(
        (sum, row) => sum + Number(row.reference.goals.differential || 0),
        0,
      ),
      progression_share: average(
        pairs,
        (row) => row.reference.progression.test_share,
      ),
      possession_proxy_rate: average(
        pairs,
        (row) => row.reference.possession_proxy.test_rate,
      ),
      formation_order_rate: average(
        pairs,
        (row) => row.reference.team_shape.formation_order_rate,
      ),
      collapsed_shape_rate: average(
        pairs,
        (row) => row.reference.team_shape.collapsed_rate,
      ),
      overstretched_shape_rate: average(
        pairs,
        (row) => row.reference.team_shape.overstretched_rate,
      ),
    },
  };
}

function summarizeRoleRows(rows, role) {
  const roleRows = rows
    .map((row) => row.test_team.roles?.[role])
    .filter(Boolean);
  return {
    samples: roleRows.length,
    average_ball_distance: average(
      roleRows,
      (row) => row.average_ball_distance,
    ),
    near_ball_rate: average(roleRows, (row) => row.near_ball_rate),
    close_ball_rate: average(roleRows, (row) => row.close_ball_rate),
    average_role_deviation: average(
      roleRows,
      (row) => row.average_role_deviation,
    ),
    boundary_rate: average(roleRows, (row) => row.boundary_rate),
    ood_rate: average(roleRows, (row) => row.ood_rate),
    far_stall_rate: average(roleRows, (row) => row.far_stall_rate),
    max_far_stall_seconds: Math.max(
      0,
      ...roleRows.map((row) => Number(row.max_far_stall_seconds || 0)),
    ),
    max_held_action_seconds: Math.max(
      0,
      ...roleRows.map((row) => Number(row.max_held_action_seconds || 0)),
    ),
    context_adaptation_rate: average(
      roleRows,
      (row) => row.context_adaptation_rate,
    ),
    runtime_errors: roleRows.reduce(
      (sum, row) => sum + Number(row.runtime_errors || 0),
      0,
    ),
  };
}

function summarizeRolePairs(pairs, role) {
  const candidateRows = pairs.map((pair) => pair.candidate);
  const referenceRows = pairs.map((pair) => pair.reference);
  const candidate = summarizeRoleRows(candidateRows, role);
  const reference = summarizeRoleRows(referenceRows, role);
  return {
    candidate,
    reference,
    delta: {
      average_ball_distance:
        candidate.average_ball_distance - reference.average_ball_distance,
      near_ball_rate:
        candidate.near_ball_rate - reference.near_ball_rate,
      average_role_deviation:
        candidate.average_role_deviation - reference.average_role_deviation,
      boundary_rate:
        candidate.boundary_rate - reference.boundary_rate,
      ood_rate:
        candidate.ood_rate - reference.ood_rate,
      far_stall_rate:
        candidate.far_stall_rate - reference.far_stall_rate,
      max_held_action_seconds:
        candidate.max_held_action_seconds - reference.max_held_action_seconds,
      context_adaptation_rate:
        candidate.context_adaptation_rate - reference.context_adaptation_rate,
    },
  };
}

function summarizeArena({
  fullTeamPairs,
  plugPairs,
  args,
  scenarios,
}) {
  const plugByRole = {};
  for (const role of ROLES) {
    const rows = plugPairs.filter((row) => row.tested_role === role);
    plugByRole[role] = {
      team_outcome: summarizePairs(rows),
      individual: summarizeRolePairs(rows, role),
    };
  }

  const fullRoles = Object.fromEntries(
    ROLES.map((role) => [role, summarizeRolePairs(fullTeamPairs, role)]),
  );

  return {
    schema: ARENA_SCHEMA,
    evaluation_mode:
      "paired_raw_policy_full_team_plus_plug_and_play_context_generalization_v2",
    raw_policy_only: true,
    safety_recovery_enabled: false,
    paired_reference_design: true,
    provenance: {
      challenger_model: args.challenger,
      challenger_sha256: sha256File(args.challenger),
      champion_model: args.champion,
      champion_sha256: sha256File(args.champion),
      partner_models: args.partnerModels,
      partner_sha256s: args.partnerModels.map(sha256File),
      stadium: args.stadium,
      stadium_sha256: sha256File(args.stadium),
      scenarios: args.scenarios,
      scenarios_sha256: sha256File(args.scenarios),
    },
    config: {
      seconds: args.seconds,
      sample_every: args.sampleEvery,
      max_scenarios: args.maxScenarios,
      plug_repeats: args.plugRepeats,
      seed: args.seed,
      scenario_count: scenarios.length,
      roles: ROLES,
      pair_tie_margin: 0.025,
    },
    team_mode: {
      summary: summarizePairs(fullTeamPairs),
      roles: fullRoles,
    },
    plug_and_play: {
      partner_model_count: new Set(args.partnerModels).size,
      summary: summarizePairs(plugPairs),
      by_role: plugByRole,
    },
    match_results: {
      team_mode: fullTeamPairs,
      plug_and_play: plugPairs,
    },
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const stadiumJson = JSON.parse(fs.readFileSync(args.stadium, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));
  const scenarios = loadScenarioRows(args.scenarios, args.maxScenarios);

  const fullTeamPairs = [];
  const plugPairs = [];

  for (let scenarioOffset = 0; scenarioOffset < scenarios.length; scenarioOffset += 1) {
    const scenario = scenarios[scenarioOffset];
    const scenarioIndex = scenarioIndexForRow(
      scenario,
      scenarioOffset,
    );
    for (const side of [1, 2]) {
      const candidate = runArenaMatch({
        mode: "full_team",
        testedRole: null,
        testedPath: args.challenger,
        testedKind: "challenger",
        championPath: args.champion,
        partnerPool: args.partnerModels,
        stadium,
        scenario,
        scenarioIndex,
        testTeamId: side,
        seconds: args.seconds,
        sampleEvery: args.sampleEvery,
        seed: args.seed,
        repeatIndex: 0,
      });
      const reference = runArenaMatch({
        mode: "full_team",
        testedRole: null,
        testedPath: args.champion,
        testedKind: "reference",
        championPath: args.champion,
        partnerPool: args.partnerModels,
        stadium,
        scenario,
        scenarioIndex,
        testTeamId: side,
        seconds: args.seconds,
        sampleEvery: args.sampleEvery,
        seed: args.seed,
        repeatIndex: 0,
      });
      const pair = pairArenaRows(candidate, reference);
      fullTeamPairs.push(pair);
      console.error(
        "[arena-v2 team] scenario=" + scenarioIndex +
        " side=" + side +
        " delta=" + pair.proxy_delta.toFixed(4) +
        " " + pair.result,
      );
    }

    for (const role of ROLES) {
      for (let repeat = 0; repeat < args.plugRepeats; repeat += 1) {
        for (const side of [1, 2]) {
          const candidate = runArenaMatch({
            mode: "plug_and_play",
            testedRole: role,
            testedPath: args.challenger,
            testedKind: "challenger",
            championPath: args.champion,
            partnerPool: args.partnerModels,
            stadium,
            scenario,
            scenarioIndex,
            testTeamId: side,
            seconds: args.seconds,
            sampleEvery: args.sampleEvery,
            seed: args.seed,
            repeatIndex: repeat,
          });
          const reference = runArenaMatch({
            mode: "plug_and_play",
            testedRole: role,
            testedPath: args.champion,
            testedKind: "reference",
            championPath: args.champion,
            partnerPool: args.partnerModels,
            stadium,
            scenario,
            scenarioIndex,
            testTeamId: side,
            seconds: args.seconds,
            sampleEvery: args.sampleEvery,
            seed: args.seed,
            repeatIndex: repeat,
          });
          const pair = pairArenaRows(candidate, reference);
          plugPairs.push(pair);
          console.error(
            "[arena-v2 plug] scenario=" + scenarioIndex +
            " role=" + role +
            " side=" + side +
            " delta=" + pair.proxy_delta.toFixed(4) +
            " " + pair.result,
          );
        }
      }
    }
  }

  const summary = summarizeArena({
    fullTeamPairs,
    plugPairs,
    args,
    scenarios,
  });
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
  ARENA_SCHEMA,
  ROLES,
  parseArgs,
  scenarioIndexForRow,
  loadScenarioRows,
  actionKey,
  angleDelta,
  contextShifted,
  choosePartnerModel,
  createTracker,
  updateTracker,
  finishTracker,
  pairArenaRows,
  summarizePairs,
  summarizeRolePairs,
};
