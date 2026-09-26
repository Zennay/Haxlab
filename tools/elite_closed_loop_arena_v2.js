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
  primeElitePolicy,
} = require("./sandbox_replay_start");
const {
  scriptedAction,
} = require("./elite_sandbox_benchmark");

const ROLES = ["gk", "dm", "am", "st"];
const PROFILES = ["balanced", "compact", "press"];
const ARENA_SCHEMA = "haxlab-closed-loop-arena-v2";
const SUITE_SCHEMA = "haxlab-promotion-v2-frozen-suite-v1";

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function fileSha256(filePath) {
  return sha256Bytes(fs.readFileSync(filePath));
}

function stableJson(value) {
  if (Array.isArray(value)) return value.map(stableJson);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stableJson(value[key])]),
    );
  }
  return value;
}

function sha256Json(value) {
  return sha256Bytes(JSON.stringify(stableJson(value)));
}

function usage() {
  console.error(
    "Usage: node tools/elite_closed_loop_arena_v2.js " +
      "--model runtime-model.json --suite-root frozen-suite " +
      "--output-root output-dir --code-ref <commit> " +
      "[--seconds 90] [--max-scenarios 16] [--sample-every 6] " +
      "[--seed 1337]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const args = {
    seconds: 90,
    maxScenarios: 16,
    sampleEvery: 6,
    seed: 1337,
    guardBoundaryX: 760,
    guardBoundaryY: 320,
    guardOodMaxZ: 8,
    guardRoleLeash: 190,
    guardStallDistance: 240,
    guardStallDecisions: 5,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error("missing value for " + key);
      return argv[i];
    };
    if (key === "--model") args.model = next();
    else if (key === "--suite-root") args.suiteRoot = next();
    else if (key === "--output-root") args.outputRoot = next();
    else if (key === "--code-ref") args.codeRef = next();
    else if (key === "--seconds") args.seconds = Number(next());
    else if (key === "--max-scenarios") args.maxScenarios = Number(next());
    else if (key === "--sample-every") args.sampleEvery = Number(next());
    else if (key === "--seed") args.seed = Number(next());
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("unknown argument: " + key);
  }
  if (!args.model || !args.suiteRoot || !args.outputRoot || !args.codeRef) {
    usage();
  }
  args.seconds = Math.max(10, Number(args.seconds) || 90);
  args.maxScenarios = Math.max(1, Math.floor(args.maxScenarios || 16));
  args.sampleEvery = Math.max(1, Math.floor(args.sampleEvery || 6));
  args.seed = Math.floor(Number(args.seed) || 1337);
  return args;
}

function validateFrozenSuite(suiteRoot) {
  const root = path.resolve(suiteRoot);
  const manifestPath = path.join(root, "frozen-manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error("missing frozen suite manifest: " + manifestPath);
  }
  const manifestBytes = fs.readFileSync(manifestPath);
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  if (manifest.schema !== SUITE_SCHEMA) {
    throw new Error("unsupported frozen suite schema: " + manifest.schema);
  }
  if (manifest.frozen_before_next_challenger !== true) {
    throw new Error("suite was not declared frozen before challenger");
  }
  const sources = Array.isArray(manifest.sources) ? manifest.sources : [];
  if (sources.length < 3 || Number(manifest.source_count) !== sources.length) {
    throw new Error("frozen suite requires at least three declared sources");
  }
  const seenReplay = new Set();
  const seenScenario = new Set();
  const validated = [];
  for (const source of sources) {
    const sourceId = String(source.id || "");
    if (!/^source-\d{2}$/.test(sourceId)) {
      throw new Error("invalid source id: " + sourceId);
    }
    const replaySha = String(source.replay_sha256 || "").toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(replaySha) || seenReplay.has(replaySha)) {
      throw new Error("invalid or duplicate replay sha: " + replaySha);
    }
    seenReplay.add(replaySha);
    const dir = path.join(root, sourceId);
    const expectedFiles = source.files_sha256 || {};
    for (const name of ["source.json", "stadium.hbs", "scenarios.json"]) {
      const filePath = path.join(dir, name);
      if (!fs.existsSync(filePath)) {
        throw new Error("missing frozen source file: " + filePath);
      }
      const actual = fileSha256(filePath);
      if (actual !== String(expectedFiles[name] || "")) {
        throw new Error("frozen source hash mismatch: " + sourceId + "/" + name);
      }
    }
    const sourcePayload = JSON.parse(
      fs.readFileSync(path.join(dir, "source.json"), "utf8"),
    );
    if (String(sourcePayload.sha256 || "").toLowerCase() !== replaySha) {
      throw new Error("source replay sha mismatch: " + sourceId);
    }
    const scenarioPath = path.join(dir, "scenarios.json");
    const scenarioSha = fileSha256(scenarioPath);
    if (seenScenario.has(scenarioSha)) {
      throw new Error("duplicate scenario file sha: " + scenarioSha);
    }
    seenScenario.add(scenarioSha);
    const scenarios = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
    if (
      scenarios.schema !== "haxlab-replay-seeded-scenarios-v1" ||
      !Array.isArray(scenarios.scenarios) ||
      scenarios.scenarios.length < 1
    ) {
      throw new Error("invalid scenarios payload: " + sourceId);
    }
    validated.push({
      id: sourceId,
      replay_sha256: replaySha,
      dir,
      scenario_path: scenarioPath,
      scenario_sha256: scenarioSha,
      stadium_path: path.join(dir, "stadium.hbs"),
      scenarios,
    });
  }
  return {
    root,
    manifest,
    manifest_path: manifestPath,
    manifest_sha256: sha256Bytes(manifestBytes),
    sources: validated,
  };
}

function addPlayer(room, id, name, teamId) {
  room.playerJoin(id, name, "xx", "AI", "arena-" + id, "arena-" + id);
  room.setPlayerTeam(id, teamId, 0);
}

function actionKey(action, teamId) {
  const canonicalX = Number(teamId) === 2
    ? -Number(action?.dirX || 0)
    : Number(action?.dirX || 0);
  return [
    Math.max(-1, Math.min(1, canonicalX)),
    Math.max(-1, Math.min(1, Number(action?.dirY || 0))),
    Boolean(action?.kick) ? 1 : 0,
  ].join(",");
}

function updateHeld(bot, key, prefix) {
  const lastKeyName = prefix + "LastActionKey";
  const heldName = prefix + "HeldStreak";
  const maxName = prefix + "MaxHeldStreak";
  const changesName = prefix + "ActionChanges";
  if (bot[lastKeyName] == null) {
    bot[lastKeyName] = key;
    bot[heldName] = 1;
  } else if (bot[lastKeyName] === key) {
    bot[heldName] += 1;
  } else {
    bot[changesName] += 1;
    bot[lastKeyName] = key;
    bot[heldName] = 1;
  }
  bot[maxName] = Math.max(bot[maxName], bot[heldName]);
}

function roleTargetDistance(bot, player, gameState, teamId, stadium) {
  const recovery = recoveryAction(
    bot.role,
    player,
    gameState,
    teamId,
    { stadiumWidth: Number(stadium.width || 800) },
  );
  if (!recovery) return { distance: null, recovery: null };
  const disc = discOf(player);
  const distance = Math.hypot(
    Number(recovery.target_canonical_x || 0) -
      Number(recovery.canonical_player_x || 0),
    Number(recovery.target_y || 0) -
      Number(disc?.pos?.y || 0),
  );
  return { distance, recovery };
}

function teamShapeSample(eliteBots, players, eliteTeamId) {
  const sign = Number(eliteTeamId) === 2 ? -1 : 1;
  const points = [];
  for (const bot of eliteBots) {
    const player = players.find(
      (candidate) => Number(candidate.id) === Number(bot.id),
    );
    const disc = discOf(player);
    if (!disc?.pos) return null;
    points.push({
      role: bot.role,
      x: sign * num(disc.pos.x),
      y: num(disc.pos.y),
    });
  }
  const byRole = Object.fromEntries(points.map((row) => [row.role, row]));
  const ordered =
    byRole.gk.x <= byRole.dm.x &&
    byRole.dm.x <= byRole.am.x &&
    byRole.am.x <= byRole.st.x;
  const xs = points.map((row) => row.x);
  let pairwise = 0;
  let pairs = 0;
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      pairwise += Math.hypot(
        points[i].x - points[j].x,
        points[i].y - points[j].y,
      );
      pairs += 1;
    }
  }
  return {
    ordered,
    x_span: Math.max(...xs) - Math.min(...xs),
    mean_pairwise_distance: pairwise / Math.max(1, pairs),
  };
}

function blankBot(id, role, teamId) {
  return {
    id,
    role,
    teamId,
    isElite: true,
    actions: 0,
    kicks: 0,
    nearBall: 0,
    ballDistanceSum: 0,
    minBallDistance: Infinity,
    rawLastActionKey: null,
    rawHeldStreak: 0,
    rawMaxHeldStreak: 0,
    rawActionChanges: 0,
    executedLastActionKey: null,
    executedHeldStreak: 0,
    executedMaxHeldStreak: 0,
    executedActionChanges: 0,
    stallStreak: 0,
    maxStallStreak: 0,
    stallSamples: 0,
    oodMeanSum: 0,
    oodMax: 0,
    oodSpikeSamples: 0,
    roleTargetDistanceSum: 0,
    roleTargetSamples: 0,
    roleTargetMax: 0,
    roleDeviationSamples: 0,
    boundarySamples: 0,
    boundaryEvents: 0,
    boundaryActive: false,
    guardOverrides: 0,
    guardReasons: {
      boundary: 0,
      role_leash: 0,
      stall: 0,
      ood: 0,
    },
  };
}

function runEpisode({
  runtimeModel,
  stadium,
  scenario,
  scenarioIndex,
  eliteTeamId,
  baselineProfile,
  seconds,
  sampleEvery,
  guardEnabled,
  guardConfig,
}) {
  const policy = new ElitePolicyRuntime(runtimeModel);
  const baselineTeamId = eliteTeamId === 1 ? 2 : 1;
  let redGoals = 0;
  let blueGoals = 0;
  let runtimeErrors = 0;

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

  const eliteBots = [];
  const baselineBots = [];
  for (let index = 0; index < 4; index += 1) {
    const role = ROLES[index];
    const eliteId = 100 + index;
    const baselineId = 200 + index;
    addPlayer(room, eliteId, "Arena-" + role.toUpperCase(), eliteTeamId);
    addPlayer(room, baselineId, "Script-" + role.toUpperCase(), baselineTeamId);
    eliteBots.push(blankBot(eliteId, role, eliteTeamId));
    baselineBots.push({
      id: baselineId,
      role,
      teamId: baselineTeamId,
      isElite: false,
      actions: 0,
      kicks: 0,
    });
  }

  room.startGame(0);
  room.runSteps(5);
  prepareReplayScenario(
    room,
    [...eliteBots, ...baselineBots],
    scenario,
    eliteTeamId,
  );
  primeElitePolicy(policy, eliteBots, scenario);

  const totalTicks = Math.floor(seconds * 60);
  const sourceScore = scenario.score || {};
  const sourceEliteScoreDiff =
    Number(sourceScore.red || 0) - Number(sourceScore.blue || 0);
  let samples = 0;
  let eliteHalf = 0;
  let baselineHalf = 0;
  let eliteAttackThird = 0;
  let baselineAttackThird = 0;
  let previousEliteAxisX = null;
  let eliteProgress = 0;
  let baselineProgress = 0;
  let teamShapeSamples = 0;
  let formationOrderCorrect = 0;
  let teamXSpanSum = 0;
  let teamPairwiseSum = 0;

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
          const playerDisc = discOf(player);
          if (!player || !playerDisc?.pos) continue;
          try {
            const features = buildFeatureObject(player, state, gameState);
            if (!features) continue;
            const currentGoalDiff =
              eliteTeamId === 1
                ? redGoals - blueGoals
                : blueGoals - redGoals;
            features.score_diff = sourceEliteScoreDiff + currentGoalDiff;

            const canonical = policy.act({
              agent_id: String(bot.id),
              role: bot.role,
              features,
            });
            let action = canonicalActionToWorld(canonical, eliteTeamId);
            const rawKey = actionKey(action, eliteTeamId);
            updateHeld(bot, rawKey, "raw");

            const ballDistance = Math.hypot(
              num(ball.pos.x) - num(playerDisc.pos.x),
              num(ball.pos.y) - num(playerDisc.pos.y),
            );
            bot.ballDistanceSum += ballDistance;
            bot.minBallDistance = Math.min(bot.minBallDistance, ballDistance);
            if (ballDistance <= 32) bot.nearBall += 1;

            const stationary =
              Number(action.dirX || 0) === 0 &&
              Number(action.dirY || 0) === 0;
            bot.stallStreak =
              stationary && ballDistance >= 120
                ? bot.stallStreak + 1
                : 0;
            bot.maxStallStreak = Math.max(
              bot.maxStallStreak,
              bot.stallStreak,
            );
            if (bot.stallStreak >= 5) bot.stallSamples += 1;

            const oodMean = Number(canonical.ood_mean_abs_z || 0);
            const oodMax = Number(canonical.ood_max_abs_z || 0);
            bot.oodMeanSum += oodMean;
            bot.oodMax = Math.max(bot.oodMax, oodMax);
            if (oodMax > guardConfig.oodMaxZ) bot.oodSpikeSamples += 1;

            const target = roleTargetDistance(
              bot,
              player,
              gameState,
              eliteTeamId,
              stadium,
            );
            if (target.distance != null) {
              bot.roleTargetDistanceSum += target.distance;
              bot.roleTargetSamples += 1;
              bot.roleTargetMax = Math.max(
                bot.roleTargetMax,
                target.distance,
              );
              if (target.distance > guardConfig.roleLeash) {
                bot.roleDeviationSamples += 1;
              }
            }

            const outOfBounds =
              Math.abs(num(playerDisc.pos.x)) > guardConfig.boundaryX ||
              Math.abs(num(playerDisc.pos.y)) > guardConfig.boundaryY;
            if (outOfBounds) {
              bot.boundarySamples += 1;
              if (!bot.boundaryActive) bot.boundaryEvents += 1;
              bot.boundaryActive = true;
            } else {
              bot.boundaryActive = false;
            }

            if (guardEnabled && target.recovery) {
              const stalledFar =
                stationary &&
                ballDistance >= guardConfig.stallDistance &&
                bot.stallStreak >= guardConfig.stallDecisions;
              const oodDrift =
                oodMax > guardConfig.oodMaxZ &&
                ballDistance >= 500;
              const roleDrift =
                ballDistance >= 120 &&
                target.distance > guardConfig.roleLeash;
              let reason = null;
              if (outOfBounds) reason = "boundary";
              else if (roleDrift) reason = "role_leash";
              else if (stalledFar) reason = "stall";
              else if (oodDrift) reason = "ood";
              if (reason) {
                action = target.recovery;
                bot.guardOverrides += 1;
                bot.guardReasons[reason] += 1;
                bot.stallStreak = 0;
              }
            }

            action = enforceKickRange(action, player, gameState);
            const executedKey = actionKey(action, eliteTeamId);
            updateHeld(bot, executedKey, "executed");

            room.playerInput(
              Utils.keyState(action.dirX, action.dirY, action.kick),
              bot.id,
            );
            bot.actions += 1;
            if (action.kick) bot.kicks += 1;
          } catch (error) {
            runtimeErrors += 1;
            if (runtimeErrors <= 3) {
              console.error(
                "arena scenario " + scenarioIndex + " runtime error:",
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
          room.playerInput(
            Utils.keyState(action.dirX, action.dirY, action.kick),
            bot.id,
          );
          bot.actions += 1;
          if (action.kick) bot.kicks += 1;
        }

        const shape = teamShapeSample(eliteBots, players, eliteTeamId);
        if (shape) {
          teamShapeSamples += 1;
          if (shape.ordered) formationOrderCorrect += 1;
          teamXSpanSum += shape.x_span;
          teamPairwiseSum += shape.mean_pairwise_distance;
        }

        const eliteAxisX =
          (eliteTeamId === 1 ? 1 : -1) * num(ball.pos.x);
        if (previousEliteAxisX != null) {
          const delta = eliteAxisX - previousEliteAxisX;
          if (delta > 0) eliteProgress += delta;
          else if (delta < 0) baselineProgress += -delta;
        }
        previousEliteAxisX = eliteAxisX;

        samples += 1;
        if (eliteAxisX > 10) eliteHalf += 1;
        else if (eliteAxisX < -10) baselineHalf += 1;
        const attackThird = Math.max(
          110,
          Number(stadium.width || 420) * 0.34,
        );
        if (eliteAxisX > attackThird) eliteAttackThird += 1;
        else if (eliteAxisX < -attackThird) baselineAttackThird += 1;
      }
    }
    room.runSteps(1);
  }

  const denom = Math.max(1, samples);
  const eliteGoals = eliteTeamId === 1 ? redGoals : blueGoals;
  const baselineGoals = eliteTeamId === 1 ? blueGoals : redGoals;
  const totalActions = eliteBots.reduce((sum, bot) => sum + bot.actions, 0);
  const guardOverrides = eliteBots.reduce(
    (sum, bot) => sum + bot.guardOverrides,
    0,
  );
  const result = {
    scenario_index: scenarioIndex,
    source_frame: Number(scenario.frame || 0),
    scenario_class: baselineProfile,
    elite_team_id: eliteTeamId,
    guard_enabled: Boolean(guardEnabled),
    seconds,
    sample_every_ticks: sampleEvery,
    result:
      eliteGoals > baselineGoals ? "win" :
      eliteGoals < baselineGoals ? "loss" : "draw",
    goals: {
      elite: eliteGoals,
      baseline: baselineGoals,
      differential: eliteGoals - baselineGoals,
    },
    territory: {
      elite_half_rate: eliteHalf / denom,
      baseline_half_rate: baselineHalf / denom,
      elite_attack_third_rate: eliteAttackThird / denom,
      baseline_attack_third_rate: baselineAttackThird / denom,
    },
    progression: {
      elite_positive_x: eliteProgress,
      baseline_positive_x: baselineProgress,
      elite_share:
        eliteProgress / Math.max(1e-9, eliteProgress + baselineProgress),
    },
    team_shape: {
      samples: teamShapeSamples,
      formation_order_rate:
        formationOrderCorrect / Math.max(1, teamShapeSamples),
      mean_x_span: teamXSpanSum / Math.max(1, teamShapeSamples),
      mean_pairwise_distance:
        teamPairwiseSum / Math.max(1, teamShapeSamples),
    },
    telemetry: {
      runtime_errors: runtimeErrors,
      total_actions: totalActions,
      guard_overrides: guardOverrides,
      guard_intervention_rate:
        guardOverrides / Math.max(1, totalActions),
      roles: Object.fromEntries(
        eliteBots.map((bot) => [
          bot.role,
          {
            actions: bot.actions,
            kicks: bot.kicks,
            average_ball_distance:
              bot.ballDistanceSum / Math.max(1, bot.actions),
            min_ball_distance:
              Number.isFinite(bot.minBallDistance)
                ? bot.minBallDistance
                : null,
            near_ball_rate: bot.nearBall / Math.max(1, bot.actions),
            raw_action_change_rate:
              bot.rawActionChanges / Math.max(1, bot.actions - 1),
            raw_max_held_decisions: bot.rawMaxHeldStreak,
            executed_action_change_rate:
              bot.executedActionChanges / Math.max(1, bot.actions - 1),
            executed_max_held_decisions: bot.executedMaxHeldStreak,
            max_stall_decisions: bot.maxStallStreak,
            long_stall_rate: bot.stallSamples / Math.max(1, bot.actions),
            ood_mean_abs_z:
              bot.oodMeanSum / Math.max(1, bot.actions),
            ood_max_abs_z: bot.oodMax,
            ood_spike_rate:
              bot.oodSpikeSamples / Math.max(1, bot.actions),
            average_role_target_distance:
              bot.roleTargetDistanceSum /
              Math.max(1, bot.roleTargetSamples),
            max_role_target_distance: bot.roleTargetMax,
            role_deviation_rate:
              bot.roleDeviationSamples /
              Math.max(1, bot.roleTargetSamples),
            boundary_sample_rate:
              bot.boundarySamples / Math.max(1, bot.actions),
            boundary_events: bot.boundaryEvents,
            guard_overrides: bot.guardOverrides,
            guard_intervention_rate:
              bot.guardOverrides / Math.max(1, bot.actions),
            guard_reasons: bot.guardReasons,
          },
        ]),
      ),
    },
  };

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}
  return result;
}

function mean(rows, fn) {
  if (!rows.length) return 0;
  return rows.reduce((sum, row) => sum + Number(fn(row) || 0), 0) /
    rows.length;
}

function summarizeEpisodes(episodes) {
  const wins = episodes.filter((row) => row.result === "win").length;
  const losses = episodes.filter((row) => row.result === "loss").length;
  const draws = episodes.length - wins - losses;
  const totalActions = episodes.reduce(
    (sum, row) => sum + row.telemetry.total_actions,
    0,
  );
  const totalGuard = episodes.reduce(
    (sum, row) => sum + row.telemetry.guard_overrides,
    0,
  );
  const roles = {};
  for (const role of ROLES) {
    roles[role] = {
      average_ball_distance: mean(
        episodes,
        (row) => row.telemetry.roles[role].average_ball_distance,
      ),
      near_ball_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].near_ball_rate,
      ),
      raw_action_change_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].raw_action_change_rate,
      ),
      raw_max_held_decisions: Math.max(
        0,
        ...episodes.map(
          (row) => row.telemetry.roles[role].raw_max_held_decisions,
        ),
      ),
      max_stall_decisions: Math.max(
        0,
        ...episodes.map(
          (row) => row.telemetry.roles[role].max_stall_decisions,
        ),
      ),
      long_stall_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].long_stall_rate,
      ),
      ood_mean_abs_z: mean(
        episodes,
        (row) => row.telemetry.roles[role].ood_mean_abs_z,
      ),
      ood_max_abs_z: Math.max(
        0,
        ...episodes.map(
          (row) => row.telemetry.roles[role].ood_max_abs_z,
        ),
      ),
      ood_spike_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].ood_spike_rate,
      ),
      average_role_target_distance: mean(
        episodes,
        (row) => row.telemetry.roles[role].average_role_target_distance,
      ),
      role_deviation_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].role_deviation_rate,
      ),
      boundary_sample_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].boundary_sample_rate,
      ),
      boundary_events: episodes.reduce(
        (sum, row) =>
          sum + row.telemetry.roles[role].boundary_events,
        0,
      ),
      guard_intervention_rate: mean(
        episodes,
        (row) => row.telemetry.roles[role].guard_intervention_rate,
      ),
    };
  }
  return {
    episodes: episodes.length,
    wins,
    draws,
    losses,
    match_score:
      (wins + 0.5 * draws) / Math.max(1, episodes.length),
    goal_differential: episodes.reduce(
      (sum, row) => sum + row.goals.differential,
      0,
    ),
    territory: {
      elite_half_rate: mean(
        episodes,
        (row) => row.territory.elite_half_rate,
      ),
      elite_attack_third_rate: mean(
        episodes,
        (row) => row.territory.elite_attack_third_rate,
      ),
    },
    progression_share: mean(
      episodes,
      (row) => row.progression.elite_share,
    ),
    team_shape: {
      formation_order_rate: mean(
        episodes,
        (row) => row.team_shape.formation_order_rate,
      ),
      mean_x_span: mean(
        episodes,
        (row) => row.team_shape.mean_x_span,
      ),
      mean_pairwise_distance: mean(
        episodes,
        (row) => row.team_shape.mean_pairwise_distance,
      ),
    },
    runtime_errors: episodes.reduce(
      (sum, row) => sum + row.telemetry.runtime_errors,
      0,
    ),
    guard_overrides: totalGuard,
    guard_intervention_rate:
      totalGuard / Math.max(1, totalActions),
    roles,
  };
}

function validateEpisodeTelemetry(episode) {
  const requiredTop = [
    "scenario_index",
    "elite_team_id",
    "team_shape",
    "telemetry",
    "progression",
    "territory",
  ];
  for (const key of requiredTop) {
    if (episode[key] == null) {
      throw new Error("arena telemetry missing " + key);
    }
  }
  if (Number(episode.telemetry.total_actions || 0) <= 0) {
    throw new Error("arena telemetry has no policy actions");
  }
  for (const role of ROLES) {
    const row = episode.telemetry.roles?.[role];
    if (!row || Number(row.actions || 0) <= 0) {
      throw new Error("arena telemetry missing role actions: " + role);
    }
    for (const [key, value] of Object.entries(row)) {
      if (
        key !== "min_ball_distance" &&
        key !== "guard_reasons" &&
        typeof value === "number" &&
        !Number.isFinite(value)
      ) {
        throw new Error("arena telemetry non-finite " + role + "/" + key);
      }
    }
  }
}

function guardDependency(raw, guarded) {
  return {
    guard_intervention_rate: guarded.guard_intervention_rate,
    match_score_delta: guarded.match_score - raw.match_score,
    progression_delta:
      guarded.progression_share - raw.progression_share,
    territory_delta:
      guarded.territory.elite_half_rate - raw.territory.elite_half_rate,
    formation_order_delta:
      guarded.team_shape.formation_order_rate -
      raw.team_shape.formation_order_rate,
  };
}

function writeImmutable(filePath, payload) {
  const rendered = JSON.stringify(payload, null, 2) + "\n";
  if (fs.existsSync(filePath)) {
    const existing = fs.readFileSync(filePath, "utf8");
    if (existing !== rendered) {
      throw new Error("immutable arena artifact conflict: " + filePath);
    }
    return;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, rendered);
}

function runArena(args) {
  const suite = validateFrozenSuite(args.suiteRoot);
  const modelPath = path.resolve(args.model);
  const modelBytes = fs.readFileSync(modelPath);
  const runtimeModel = JSON.parse(modelBytes.toString("utf8"));
  const modelSha = sha256Bytes(modelBytes);
  const config = {
    schema: "haxlab-closed-loop-arena-v2-config-v1",
    seconds: args.seconds,
    max_scenarios: args.maxScenarios,
    sample_every_ticks: args.sampleEvery,
    seed: args.seed,
    baseline_profiles: PROFILES,
    raw_policy: {
      live_guard: false,
      recovery: false,
      future_assist: false,
    },
    guarded_policy: {
      live_guard: true,
      recovery: false,
      future_assist: false,
      boundary_x: args.guardBoundaryX,
      boundary_y: args.guardBoundaryY,
      ood_max_z: args.guardOodMaxZ,
      role_leash: args.guardRoleLeash,
      stall_ball_distance: args.guardStallDistance,
      stall_decisions: args.guardStallDecisions,
    },
  };
  const configSha = sha256Json(config);
  const guardConfig = {
    boundaryX: args.guardBoundaryX,
    boundaryY: args.guardBoundaryY,
    oodMaxZ: args.guardOodMaxZ,
    roleLeash: args.guardRoleLeash,
    stallDistance: args.guardStallDistance,
    stallDecisions: args.guardStallDecisions,
  };
  const sources = [];

  for (let sourceIndex = 0; sourceIndex < suite.sources.length; sourceIndex += 1) {
    const source = suite.sources[sourceIndex];
    const stadiumJson = JSON.parse(fs.readFileSync(source.stadium_path, "utf8"));
    const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));
    const scenarios = source.scenarios.scenarios.slice(0, args.maxScenarios);
    const rawEpisodes = [];
    const guardedEpisodes = [];

    for (let index = 0; index < scenarios.length; index += 1) {
      const scenario = scenarios[index];
      const profileIndex =
        (index + Math.abs(args.seed)) % PROFILES.length;
      const baselineProfile = PROFILES[profileIndex];
      for (const eliteTeamId of [1, 2]) {
        const common = {
          runtimeModel,
          stadium,
          scenario,
          scenarioIndex: index + 1,
          eliteTeamId,
          baselineProfile,
          seconds: args.seconds,
          sampleEvery: args.sampleEvery,
          guardConfig,
        };
        const raw = runEpisode({
          ...common,
          guardEnabled: false,
        });
        const guarded = runEpisode({
          ...common,
          guardEnabled: true,
        });
        validateEpisodeTelemetry(raw);
        validateEpisodeTelemetry(guarded);
        rawEpisodes.push(raw);
        guardedEpisodes.push(guarded);
        console.error(
          "arena-v2 source=" + source.id +
          " scenario=" + (index + 1) +
          " side=" + eliteTeamId +
          " raw_prog=" + raw.progression.elite_share.toFixed(3) +
          " guard=" +
          guarded.telemetry.guard_intervention_rate.toFixed(4),
        );
      }
    }

    const rawSummary = summarizeEpisodes(rawEpisodes);
    const guardedSummary = summarizeEpisodes(guardedEpisodes);
    sources.push({
      source_id: source.id,
      replay_sha256: source.replay_sha256,
      scenario_sha256: source.scenario_sha256,
      scenario_count: scenarios.length,
      raw: rawSummary,
      guarded: guardedSummary,
      guard_dependency: guardDependency(rawSummary, guardedSummary),
      raw_episodes: rawEpisodes,
      guarded_episodes: guardedEpisodes,
    });
  }

  const rawAll = summarizeEpisodes(
    sources.flatMap((source) => source.raw_episodes),
  );
  const guardedAll = summarizeEpisodes(
    sources.flatMap((source) => source.guarded_episodes),
  );
  const payload = {
    schema: ARENA_SCHEMA,
    provenance: {
      code_ref: String(args.codeRef),
      model_path: modelPath,
      model_sha256: modelSha,
      runtime_model_schema: runtimeModel.schema || null,
      suite_root: suite.root,
      suite_manifest_sha256: suite.manifest_sha256,
      config_sha256: configSha,
      source_replay_sha256s: suite.sources.map(
        (source) => source.replay_sha256,
      ),
      scenario_sha256s: suite.sources.map(
        (source) => source.scenario_sha256,
      ),
    },
    config,
    aggregate: {
      raw: rawAll,
      guarded: guardedAll,
      guard_dependency: guardDependency(rawAll, guardedAll),
    },
    sources,
  };

  if (
    payload.provenance.source_replay_sha256s.length < 3 ||
    payload.aggregate.raw.episodes <= 0 ||
    payload.aggregate.guarded.episodes !== payload.aggregate.raw.episodes
  ) {
    throw new Error("arena v2 incomplete provenance or rollout coverage");
  }

  const outputPath = path.join(
    path.resolve(args.outputRoot),
    modelSha,
    suite.manifest_sha256,
    configSha,
    "arena-v2.json",
  );
  writeImmutable(outputPath, payload);
  return { outputPath, payload };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const result = runArena(args);
  process.stdout.write(
    JSON.stringify({
      schema: ARENA_SCHEMA,
      output_path: result.outputPath,
      model_sha256: result.payload.provenance.model_sha256,
      suite_manifest_sha256:
        result.payload.provenance.suite_manifest_sha256,
      config_sha256: result.payload.provenance.config_sha256,
      raw_episodes: result.payload.aggregate.raw.episodes,
      guard_intervention_rate:
        result.payload.aggregate.guarded.guard_intervention_rate,
    }) + "\n",
  );
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
  SUITE_SCHEMA,
  stableJson,
  sha256Json,
  validateFrozenSuite,
  actionKey,
  updateHeld,
  teamShapeSample,
  summarizeEpisodes,
  validateEpisodeTelemetry,
  guardDependency,
  writeImmutable,
  runArena,
};
