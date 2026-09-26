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
const {
  enforceKickRange,
  recoveryAction,
  shouldRecoverFromStall,
  attachFutureSignal,
  futureMotionAssist,
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

function usage() {
  console.error(
    "Usage: node tools/elite_replay_scenario_benchmark.js " +
      "--model runtime-model.json --stadium stadium.hbs --scenarios scenarios.json " +
      "[--seconds 25] [--max-scenarios 8] [--sample-every 6] " +
      "[--disable-recovery] [--recovery-min-distance 120] " +
      "[--recovery-stall-decisions 3] [--recovery-ticks 5] " +
      "[--future-assist] [--future-model runtime-model.json] " +
      "[--future-assist-confidence 0.45] " +
      "[--future-assist-min-distance 80] [--output result.json]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const args = {
    seconds: 25,
    maxScenarios: 8,
    sampleEvery: 6,
    output: null,
    recoveryEnabled: true,
    recoveryMinDistance: 120,
    recoveryStallDecisions: 3,
    recoveryTicks: 5,
    futureAssistEnabled: false,
    futureModel: null,
    futureAssistConfidence: 0.45,
    futureAssistMinDistance: 80,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--model") { args.model = value; i += 1; }
    else if (key === "--stadium") { args.stadium = value; i += 1; }
    else if (key === "--scenarios") { args.scenarios = value; i += 1; }
    else if (key === "--seconds") { args.seconds = Number(value); i += 1; }
    else if (key === "--max-scenarios") { args.maxScenarios = Number(value); i += 1; }
    else if (key === "--sample-every") { args.sampleEvery = Number(value); i += 1; }
    else if (key === "--output") { args.output = value; i += 1; }
    else if (key === "--disable-recovery") { args.recoveryEnabled = false; }
    else if (key === "--recovery-min-distance") {
      args.recoveryMinDistance = Number(value); i += 1;
    }
    else if (key === "--recovery-stall-decisions") {
      args.recoveryStallDecisions = Number(value); i += 1;
    }
    else if (key === "--recovery-ticks") {
      args.recoveryTicks = Number(value); i += 1;
    }
    else if (key === "--future-assist") {
      args.futureAssistEnabled = true;
    }
    else if (key === "--future-model") {
      args.futureModel = value; i += 1;
    }
    else if (key === "--future-assist-confidence") {
      args.futureAssistConfidence = Number(value); i += 1;
    }
    else if (key === "--future-assist-min-distance") {
      args.futureAssistMinDistance = Number(value); i += 1;
    }
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("Unknown argument: " + key);
  }
  if (!args.model || !args.stadium || !args.scenarios) usage();
  args.seconds = Math.max(5, Number(args.seconds) || 25);
  args.maxScenarios = Math.max(1, Math.floor(args.maxScenarios));
  args.sampleEvery = Math.max(1, Math.floor(args.sampleEvery));
  args.recoveryMinDistance = Math.max(60, Number(args.recoveryMinDistance) || 120);
  args.recoveryStallDecisions = Math.max(
    2,
    Math.floor(Number(args.recoveryStallDecisions) || 3),
  );
  args.recoveryTicks = Math.max(1, Math.floor(Number(args.recoveryTicks) || 5));
  args.futureAssistConfidence = Math.max(
    0,
    Math.min(1, Number(args.futureAssistConfidence) || 0.45),
  );
  args.futureAssistMinDistance = Math.max(
    0,
    Number(args.futureAssistMinDistance) || 80,
  );
  return args;
}

function addPlayer(room, id, name, teamId) {
  room.playerJoin(id, name, "xx", "AI", "scenario-" + id, "scenario-" + id);
  room.setPlayerTeam(id, teamId, 0);
}

function runScenarioMatch({
  runtimeModel,
  futureRuntimeModel,
  stadium,
  scenario,
  scenarioIndex,
  eliteTeamId,
  baselineProfile,
  seconds,
  sampleEvery,
  recoveryEnabled,
  recoveryMinDistance,
  recoveryStallDecisions,
  recoveryTicks,
  futureAssistEnabled,
  futureAssistConfidence,
  futureAssistMinDistance,
}) {
  const policy = new ElitePolicyRuntime(runtimeModel);
  const futurePolicy = futureRuntimeModel
    ? new ElitePolicyRuntime(futureRuntimeModel)
    : null;
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
    addPlayer(room, eliteId, "Elite-" + role.toUpperCase(), eliteTeamId);
    addPlayer(room, baselineId, "Script-" + role.toUpperCase(), baselineTeamId);
    eliteBots.push({
      id: eliteId,
      role,
      teamId: eliteTeamId,
      isElite: true,
      actions: 0,
      kicks: 0,
      directionCounts: {},
      canonicalXSum: 0,
      ballDistanceSum: 0,
      nearBall: 0,
      stallStreak: 0,
      recoveryTicks: 0,
      recoveryOverrides: 0,
      futureAssistApplied: 0,
      oodMeanSum: 0,
      oodMaxSeen: 0,
      oodHigh2: 0,
      oodHigh3: 0,
      oodStationarySum: 0,
      oodStationaryCount: 0,
      oodMovingSum: 0,
      oodMovingCount: 0,
    });
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
  if (futurePolicy) primeElitePolicy(futurePolicy, eliteBots, scenario);

  const totalTicks = Math.floor(seconds * 60);
  let samples = 0;
  let eliteHalf = 0;
  let baselineHalf = 0;
  let eliteAttackThird = 0;
  let baselineAttackThird = 0;
  let previousEliteAxisX = null;
  let eliteProgress = 0;
  let baselineProgress = 0;

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
            const features = buildFeatureObject(player, state, gameState);
            if (!features) continue;
            // Sandbox score starts at 0-0. Keep live feature consistent with it.
            features.score_diff = redGoals - blueGoals;
            if (eliteTeamId === 2) features.score_diff *= -1;

            let canonical = policy.act({
              agent_id: String(bot.id),
              role: bot.role,
              features,
            });
            if (futurePolicy) {
              const futureAction = futurePolicy.act({
                agent_id: String(bot.id),
                role: bot.role,
                features,
              });
              canonical = attachFutureSignal(canonical, futureAction);
            }

            const policyStationary =
              Number(canonical.dir_x || 0) === 0 &&
              Number(canonical.dir_y || 0) === 0;
            const oodMean = Number(canonical.ood_mean_abs_z || 0);
            const oodMax = Number(canonical.ood_max_abs_z || 0);
            bot.oodMeanSum += oodMean;
            bot.oodMaxSeen = Math.max(bot.oodMaxSeen, oodMax);
            if (oodMean >= 2) bot.oodHigh2 += 1;
            if (oodMean >= 3) bot.oodHigh3 += 1;
            if (policyStationary) {
              bot.oodStationarySum += oodMean;
              bot.oodStationaryCount += 1;
            } else {
              bot.oodMovingSum += oodMean;
              bot.oodMovingCount += 1;
            }

            const ballDistanceFromFeatures = Math.hypot(
              Number(features.ball_dx || 0),
              Number(features.ball_dy || 0),
            );
            if (futureAssistEnabled) {
              canonical = futureMotionAssist(
                canonical,
                ballDistanceFromFeatures,
                {
                  minimumConfidence: futureAssistConfidence,
                  minimumBallDistance: futureAssistMinDistance,
                },
              );
              if (canonical.future_assist_applied) {
                bot.futureAssistApplied += 1;
              }
            }
            let action = canonicalActionToWorld(canonical, eliteTeamId);
            const stationary =
              Number(action.dirX || 0) === 0 &&
              Number(action.dirY || 0) === 0;
            bot.stallStreak =
              stationary && ballDistanceFromFeatures >= 120
                ? bot.stallStreak + 1
                : 0;

            if (
              recoveryEnabled &&
              bot.recoveryTicks <= 0 &&
              shouldRecoverFromStall(
                action,
                ballDistanceFromFeatures,
                bot.stallStreak,
                {
                  minimumBallDistance: recoveryMinDistance,
                  minimumStallDecisions: recoveryStallDecisions,
                },
              )
            ) {
              bot.recoveryTicks = recoveryTicks;
              bot.recoveryOverrides += 1;
              bot.stallStreak = 0;
            }

            if (recoveryEnabled && bot.recoveryTicks > 0) {
              const recovery = recoveryAction(
                bot.role,
                player,
                gameState,
                eliteTeamId,
                { stadiumWidth: Number(stadium.width || 800) },
              );
              if (recovery) {
                action = recovery;
                bot.recoveryTicks -= 1;
              } else {
                bot.recoveryTicks = 0;
              }
            }

            action = enforceKickRange(action, player, gameState);

            const pDisc = discOf(player);
            const sign = eliteTeamId === 1 ? 1 : -1;
            bot.canonicalXSum += sign * num(pDisc.pos.x);
            const ballDistance = Math.hypot(
              num(ball.pos.x) - num(pDisc.pos.x),
              num(ball.pos.y) - num(pDisc.pos.y),
            );
            bot.ballDistanceSum += ballDistance;
            if (ballDistance <= 32) bot.nearBall += 1;

            const canonicalDirX =
              eliteTeamId === 2 ? -Number(action.dirX || 0) : Number(action.dirX || 0);
            const key =
              String(canonicalDirX) + "," + String(Number(action.dirY || 0));
            bot.directionCounts[key] = (bot.directionCounts[key] || 0) + 1;

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
                "scenario " + scenarioIndex + " runtime error:",
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

  const totalActions = eliteBots.reduce((sum, bot) => sum + bot.actions, 0);
  const stationary = eliteBots.reduce(
    (sum, bot) => sum + Number(bot.directionCounts["0,0"] || 0),
    0,
  );
  const totalKicks = eliteBots.reduce((sum, bot) => sum + bot.kicks, 0);
  const nearBallSamples = eliteBots.reduce((sum, bot) => sum + bot.nearBall, 0);
  const totalOodMean = eliteBots.reduce((sum, bot) => sum + bot.oodMeanSum, 0);
  const totalOodHigh2 = eliteBots.reduce((sum, bot) => sum + bot.oodHigh2, 0);
  const totalOodHigh3 = eliteBots.reduce((sum, bot) => sum + bot.oodHigh3, 0);
  const maxOod = eliteBots.reduce(
    (best, bot) => Math.max(best, bot.oodMaxSeen),
    0,
  );
  const stationaryOodSum = eliteBots.reduce(
    (sum, bot) => sum + bot.oodStationarySum,
    0,
  );
  const stationaryOodCount = eliteBots.reduce(
    (sum, bot) => sum + bot.oodStationaryCount,
    0,
  );
  const movingOodSum = eliteBots.reduce(
    (sum, bot) => sum + bot.oodMovingSum,
    0,
  );
  const movingOodCount = eliteBots.reduce(
    (sum, bot) => sum + bot.oodMovingCount,
    0,
  );
  const eliteGoals = eliteTeamId === 1 ? redGoals : blueGoals;
  const baselineGoals = eliteTeamId === 1 ? blueGoals : redGoals;
  const denom = Math.max(1, samples);

  const result = {
    scenario_index: scenarioIndex,
    source_frame: scenario.frame,
    elite_team_id: eliteTeamId,
    baseline_profile: baselineProfile,
    recovery_enabled: Boolean(recoveryEnabled),
    recovery_config: {
      minimum_ball_distance: recoveryMinDistance,
      minimum_stall_decisions: recoveryStallDecisions,
      recovery_ticks: recoveryTicks,
    },
    future_assist_enabled: Boolean(futureAssistEnabled),
    future_signal_source: futureRuntimeModel
      ? "secondary_model"
      : "primary_model",
    future_assist_config: {
      minimum_confidence: futureAssistConfidence,
      minimum_ball_distance: futureAssistMinDistance,
    },
    seconds,
    goals: { elite: eliteGoals, baseline: baselineGoals },
    result:
      eliteGoals > baselineGoals ? "win" :
      eliteGoals < baselineGoals ? "loss" : "draw",
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
    policy: {
      runtime_errors: runtimeErrors,
      total_actions: totalActions,
      total_kicks: totalKicks,
      nonzero_movement_rate:
        (totalActions - stationary) / Math.max(1, totalActions),
      near_ball_rate: nearBallSamples / Math.max(1, totalActions),
      ood: {
        mean_abs_z: totalOodMean / Math.max(1, totalActions),
        max_abs_z: maxOod,
        high_mean_z_rate_2: totalOodHigh2 / Math.max(1, totalActions),
        high_mean_z_rate_3: totalOodHigh3 / Math.max(1, totalActions),
        stationary_mean_abs_z:
          stationaryOodSum / Math.max(1, stationaryOodCount),
        moving_mean_abs_z:
          movingOodSum / Math.max(1, movingOodCount),
        stationary_samples: stationaryOodCount,
        moving_samples: movingOodCount,
      },
      recovery_overrides: eliteBots.reduce(
        (sum, bot) => sum + bot.recoveryOverrides,
        0,
      ),
      recovery_override_rate:
        eliteBots.reduce((sum, bot) => sum + bot.recoveryOverrides, 0) /
        Math.max(1, totalActions),
      future_assist_applied: eliteBots.reduce(
        (sum, bot) => sum + bot.futureAssistApplied,
        0,
      ),
      future_assist_rate:
        eliteBots.reduce((sum, bot) => sum + bot.futureAssistApplied, 0) /
        Math.max(1, totalActions),
      roles: Object.fromEntries(
        eliteBots.map((bot) => [
          bot.role,
          {
            actions: bot.actions,
            kicks: bot.kicks,
            nonzero_movement_rate:
              (bot.actions - Number(bot.directionCounts["0,0"] || 0)) /
              Math.max(1, bot.actions),
            average_canonical_x:
              bot.canonicalXSum / Math.max(1, bot.actions),
            average_ball_distance:
              bot.ballDistanceSum / Math.max(1, bot.actions),
            near_ball_rate: bot.nearBall / Math.max(1, bot.actions),
            ood: {
              mean_abs_z: bot.oodMeanSum / Math.max(1, bot.actions),
              max_abs_z: bot.oodMaxSeen,
              high_mean_z_rate_2: bot.oodHigh2 / Math.max(1, bot.actions),
              high_mean_z_rate_3: bot.oodHigh3 / Math.max(1, bot.actions),
              stationary_mean_abs_z:
                bot.oodStationarySum / Math.max(1, bot.oodStationaryCount),
              moving_mean_abs_z:
                bot.oodMovingSum / Math.max(1, bot.oodMovingCount),
            },
            recovery_overrides: bot.recoveryOverrides,
            recovery_override_rate:
              bot.recoveryOverrides / Math.max(1, bot.actions),
            future_assist_applied: bot.futureAssistApplied,
            future_assist_rate:
              bot.futureAssistApplied / Math.max(1, bot.actions),
            direction_counts: bot.directionCounts,
          },
        ]),
      ),
    },
  };

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}
  return result;
}

function summarize(
  matches,
  modelPath,
  futureModelPath,
  scenarioPath,
  stadium,
) {
  const avg = (selector) =>
    matches.reduce((sum, row) => sum + selector(row), 0) /
    Math.max(1, matches.length);
  const wins = matches.filter((row) => row.result === "win").length;
  const losses = matches.filter((row) => row.result === "loss").length;
  const draws = matches.length - wins - losses;
  const totalActions = matches.reduce(
    (sum, row) => sum + row.policy.total_actions,
    0,
  );
  const totalKicks = matches.reduce(
    (sum, row) => sum + row.policy.total_kicks,
    0,
  );
  const nonzeroActions = matches.reduce(
    (sum, row) =>
      sum + row.policy.total_actions * row.policy.nonzero_movement_rate,
    0,
  );

  const bySide = {};
  for (const teamId of [1, 2]) {
    const rows = matches.filter((row) => row.elite_team_id === teamId);
    bySide[String(teamId)] = {
      matches: rows.length,
      elite_half_rate: rows.length
        ? rows.reduce((s, row) => s + row.territory.elite_half_rate, 0) /
          rows.length
        : 0,
      progression_share: rows.length
        ? rows.reduce((s, row) => s + row.progression.elite_share, 0) /
          rows.length
        : 0,
      nonzero_movement_rate: rows.length
        ? rows.reduce((s, row) => s + row.policy.nonzero_movement_rate, 0) /
          rows.length
        : 0,
      total_kicks: rows.reduce((s, row) => s + row.policy.total_kicks, 0),
    };
  }

  return {
    schema: "haxlab-elite-replay-scenario-benchmark-v1",
    model_path: modelPath,
    future_model_path: futureModelPath || null,
    scenario_path: scenarioPath,
    recovery_enabled: matches.length
      ? Boolean(matches[0].recovery_enabled)
      : null,
    recovery_config: matches.length
      ? matches[0].recovery_config
      : null,
    future_assist_enabled: matches.length
      ? Boolean(matches[0].future_assist_enabled)
      : null,
    future_assist_config: matches.length
      ? matches[0].future_assist_config
      : null,
    stadium: {
      name: stadium.name || null,
      width: stadium.width ?? null,
      height: stadium.height ?? null,
    },
    matches: matches.length,
    wins,
    draws,
    losses,
    non_loss_rate: (wins + draws) / Math.max(1, matches.length),
    territory: {
      elite_half_rate: avg((row) => row.territory.elite_half_rate),
      baseline_half_rate: avg((row) => row.territory.baseline_half_rate),
      elite_attack_third_rate: avg(
        (row) => row.territory.elite_attack_third_rate,
      ),
      baseline_attack_third_rate: avg(
        (row) => row.territory.baseline_attack_third_rate,
      ),
    },
    progression: {
      elite_share: avg((row) => row.progression.elite_share),
    },
    policy_activity: {
      total_actions: totalActions,
      total_kicks: totalKicks,
      nonzero_movement_rate:
        nonzeroActions / Math.max(1, totalActions),
      near_ball_rate: avg((row) => row.policy.near_ball_rate),
      ood: {
        mean_abs_z: avg((row) => Number(row.policy.ood?.mean_abs_z || 0)),
        max_abs_z: matches.reduce(
          (best, row) => Math.max(best, Number(row.policy.ood?.max_abs_z || 0)),
          0,
        ),
        high_mean_z_rate_2: avg(
          (row) => Number(row.policy.ood?.high_mean_z_rate_2 || 0),
        ),
        high_mean_z_rate_3: avg(
          (row) => Number(row.policy.ood?.high_mean_z_rate_3 || 0),
        ),
        stationary_mean_abs_z: avg(
          (row) => Number(row.policy.ood?.stationary_mean_abs_z || 0),
        ),
        moving_mean_abs_z: avg(
          (row) => Number(row.policy.ood?.moving_mean_abs_z || 0),
        ),
      },
      runtime_errors: matches.reduce(
        (sum, row) => sum + row.policy.runtime_errors,
        0,
      ),
      recovery_overrides: matches.reduce(
        (sum, row) => sum + Number(row.policy.recovery_overrides || 0),
        0,
      ),
      recovery_override_rate: avg(
        (row) => Number(row.policy.recovery_override_rate || 0),
      ),
      future_assist_applied: matches.reduce(
        (sum, row) => sum + Number(row.policy.future_assist_applied || 0),
        0,
      ),
      future_assist_rate: avg(
        (row) => Number(row.policy.future_assist_rate || 0),
      ),
    },
    paired_side_gap: {
      territory_abs:
        Math.abs(
          Number(bySide["1"]?.elite_half_rate || 0) -
          Number(bySide["2"]?.elite_half_rate || 0),
        ),
      progression_abs:
        Math.abs(
          Number(bySide["1"]?.progression_share || 0) -
          Number(bySide["2"]?.progression_share || 0),
        ),
      movement_abs:
        Math.abs(
          Number(bySide["1"]?.nonzero_movement_rate || 0) -
          Number(bySide["2"]?.nonzero_movement_rate || 0),
        ),
    },
    by_elite_side: bySide,
    match_results: matches,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const runtimeModel = JSON.parse(fs.readFileSync(args.model, "utf8"));
  const futureRuntimeModel = args.futureModel
    ? JSON.parse(fs.readFileSync(args.futureModel, "utf8"))
    : null;
  const scenarioPayload = JSON.parse(fs.readFileSync(args.scenarios, "utf8"));
  if (scenarioPayload.schema !== "haxlab-replay-seeded-scenarios-v1") {
    throw new Error("unsupported scenario schema: " + scenarioPayload.schema);
  }
  const stadiumJson = JSON.parse(fs.readFileSync(args.stadium, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));
  const scenarios = (scenarioPayload.scenarios || []).slice(
    0,
    args.maxScenarios,
  );
  if (!scenarios.length) throw new Error("scenario file contains no scenarios");

  const matches = [];
  for (let index = 0; index < scenarios.length; index += 1) {
    const scenario = scenarios[index];
    const profile = PROFILES[index % PROFILES.length];
    for (const eliteTeamId of [1, 2]) {
      const result = runScenarioMatch({
        runtimeModel,
        futureRuntimeModel,
        stadium,
        scenario,
        scenarioIndex: index + 1,
        eliteTeamId,
        baselineProfile: profile,
        seconds: args.seconds,
        sampleEvery: args.sampleEvery,
        recoveryEnabled: args.recoveryEnabled,
        recoveryMinDistance: args.recoveryMinDistance,
        recoveryStallDecisions: args.recoveryStallDecisions,
        recoveryTicks: args.recoveryTicks,
        futureAssistEnabled: args.futureAssistEnabled,
        futureAssistConfidence: args.futureAssistConfidence,
        futureAssistMinDistance: args.futureAssistMinDistance,
      });
      matches.push(result);
      console.error(
        "scenario=" + (index + 1) +
        " side=" + eliteTeamId +
        " frame=" + scenario.frame +
        " move=" + result.policy.nonzero_movement_rate.toFixed(3) +
        " kicks=" + result.policy.total_kicks +
        " prog=" + result.progression.elite_share.toFixed(3),
      );
    }
  }

  const summary = summarize(
    matches,
    args.model,
    args.futureModel,
    args.scenarios,
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
  try { main(); }
  catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}

module.exports = { runScenarioMatch, summarize };
