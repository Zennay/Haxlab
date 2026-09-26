"use strict";

const fs = require("fs");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}
function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}
function metric(obj, ...path) {
  let value = obj;
  for (const key of path) value = value?.[key];
  return num(value);
}

function compare(
  championMetrics,
  challengerMetrics,
  championClosedLoop,
  challengerPlain,
  challengerFuture,
) {
  const offline = {
    champion_direction: metric(championMetrics, "final_holdout", "direction_accuracy"),
    challenger_direction: metric(challengerMetrics, "final_holdout", "direction_accuracy"),
    champion_kick_f1: metric(championMetrics, "final_holdout", "kick_f1"),
    challenger_kick_f1: metric(challengerMetrics, "final_holdout", "kick_f1"),
    future_accuracy: metric(
      challengerMetrics,
      "final_holdout",
      "future_direction_accuracy",
    ),
    future_majority: metric(
      challengerMetrics,
      "final_holdout",
      "future_majority_direction_accuracy",
    ),
    future_lift: metric(
      challengerMetrics,
      "final_holdout",
      "future_direction_lift",
    ),
  };

  const closedLoop = {
    champion_movement: metric(
      championClosedLoop,
      "policy_activity",
      "nonzero_movement_rate",
    ),
    challenger_plain_movement: metric(
      challengerPlain,
      "policy_activity",
      "nonzero_movement_rate",
    ),
    challenger_future_movement: metric(
      challengerFuture,
      "policy_activity",
      "nonzero_movement_rate",
    ),
    champion_progression: metric(
      championClosedLoop,
      "progression",
      "elite_share",
    ),
    challenger_plain_progression: metric(
      challengerPlain,
      "progression",
      "elite_share",
    ),
    challenger_future_progression: metric(
      challengerFuture,
      "progression",
      "elite_share",
    ),
    champion_territory: metric(
      championClosedLoop,
      "territory",
      "elite_half_rate",
    ),
    challenger_future_territory: metric(
      challengerFuture,
      "territory",
      "elite_half_rate",
    ),
    champion_near_ball: metric(
      championClosedLoop,
      "policy_activity",
      "near_ball_rate",
    ),
    challenger_future_near_ball: metric(
      challengerFuture,
      "policy_activity",
      "near_ball_rate",
    ),
    future_assist_rate: metric(
      challengerFuture,
      "policy_activity",
      "future_assist_rate",
    ),
    future_runtime_errors: metric(
      challengerFuture,
      "policy_activity",
      "runtime_errors",
    ),
    future_side_gap: metric(
      challengerFuture,
      "paired_side_gap",
      "territory_abs",
    ),
  };

  const deltas = {
    offline_direction:
      offline.challenger_direction - offline.champion_direction,
    offline_kick_f1:
      offline.challenger_kick_f1 - offline.champion_kick_f1,
    plain_movement:
      closedLoop.challenger_plain_movement - closedLoop.champion_movement,
    plain_progression:
      closedLoop.challenger_plain_progression - closedLoop.champion_progression,
    future_movement:
      closedLoop.challenger_future_movement - closedLoop.champion_movement,
    future_progression:
      closedLoop.challenger_future_progression - closedLoop.champion_progression,
    future_territory:
      closedLoop.challenger_future_territory - closedLoop.champion_territory,
    future_near_ball:
      closedLoop.challenger_future_near_ball - closedLoop.champion_near_ball,
  };

  const checks = {
    immediate_direction_preserved: deltas.offline_direction >= -0.015,
    kick_skill_preserved: deltas.offline_kick_f1 >= -0.03,
    future_head_learns_signal: offline.future_lift >= 0.10,
    no_runtime_errors: closedLoop.future_runtime_errors === 0,
    future_movement_improves: deltas.future_movement >= 0.03,
    progression_preserved: deltas.future_progression >= -0.02,
    territory_preserved: deltas.future_territory >= -0.03,
    near_ball_preserved: deltas.future_near_ball >= -0.001,
    assist_is_bounded: closedLoop.future_assist_rate <= 0.20,
    side_gap_bounded: closedLoop.future_side_gap <= 0.15,
  };

  return {
    schema: "haxlab-future-motion-challenger-v1",
    offline,
    closed_loop: closedLoop,
    deltas,
    checks,
    promote_future_challenger: Object.values(checks).every(Boolean),
  };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 6) {
    console.error(
      "Usage: node tools/compare_future_challenger.js " +
      "champion-metrics.json challenger-metrics.json " +
      "champion-closed-loop.json challenger-plain.json " +
      "challenger-future.json output.json",
    );
    process.exit(2);
  }
  const result = compare(
    read(args[0]),
    read(args[1]),
    read(args[2]),
    read(args[3]),
    read(args[4]),
  );
  const rendered = JSON.stringify(result, null, 2) + "\n";
  fs.writeFileSync(args[5], rendered);
  process.stdout.write(rendered);
}

if (require.main === module) {
  try { main(); }
  catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}

module.exports = { compare };
