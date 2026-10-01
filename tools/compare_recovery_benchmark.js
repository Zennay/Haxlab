"use strict";

const fs = require("fs");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function delta(candidate, baseline, path) {
  const get = (obj) =>
    path.reduce((value, key) => (value == null ? undefined : value[key]), obj);
  return num(get(candidate)) - num(get(baseline));
}

function compare(baseline, candidate) {
  const checks = {
    same_model:
      String(baseline.model_path || "") === String(candidate.model_path || ""),
    same_scenarios:
      String(baseline.scenario_path || "") === String(candidate.scenario_path || ""),
    no_runtime_errors:
      num(candidate.policy_activity?.runtime_errors) === 0,
    movement_not_worse:
      delta(candidate, baseline, ["policy_activity", "nonzero_movement_rate"]) >= -0.005,
    progression_not_worse:
      delta(candidate, baseline, ["progression", "elite_share"]) >= -0.03,
    territory_not_worse:
      delta(candidate, baseline, ["territory", "elite_half_rate"]) >= -0.05,
    near_ball_not_worse:
      delta(candidate, baseline, ["policy_activity", "near_ball_rate"]) >= -0.001,
    fallback_bounded:
      num(candidate.policy_activity?.recovery_override_rate) <= 0.08,
    side_gap_bounded:
      num(candidate.paired_side_gap?.territory_abs) <=
      Math.max(0.15, num(baseline.paired_side_gap?.territory_abs) + 0.05),
  };

  const movementDelta = delta(
    candidate,
    baseline,
    ["policy_activity", "nonzero_movement_rate"],
  );
  const nearBallDelta = delta(
    candidate,
    baseline,
    ["policy_activity", "near_ball_rate"],
  );
  const progressionDelta = delta(
    candidate,
    baseline,
    ["progression", "elite_share"],
  );
  const territoryDelta = delta(
    candidate,
    baseline,
    ["territory", "elite_half_rate"],
  );

  const meaningfulImprovement =
    movementDelta >= 0.03 ||
    nearBallDelta >= 0.002 ||
    progressionDelta >= 0.03 ||
    territoryDelta >= 0.03;

  const safetyPassed = Object.values(checks).every(Boolean);
  return {
    schema: "haxlab-recovery-ab-v1",
    baseline_recovery_enabled: Boolean(baseline.recovery_enabled),
    candidate_recovery_enabled: Boolean(candidate.recovery_enabled),
    checks,
    deltas: {
      nonzero_movement_rate: movementDelta,
      near_ball_rate: nearBallDelta,
      progression_share: progressionDelta,
      elite_half_rate: territoryDelta,
      elite_attack_third_rate: delta(
        candidate,
        baseline,
        ["territory", "elite_attack_third_rate"],
      ),
      paired_side_territory_gap: delta(
        candidate,
        baseline,
        ["paired_side_gap", "territory_abs"],
      ),
    },
    recovery: {
      overrides: num(candidate.policy_activity?.recovery_overrides),
      override_rate: num(candidate.policy_activity?.recovery_override_rate),
    },
    baseline: {
      movement: num(baseline.policy_activity?.nonzero_movement_rate),
      near_ball: num(baseline.policy_activity?.near_ball_rate),
      progression: num(baseline.progression?.elite_share),
      territory: num(baseline.territory?.elite_half_rate),
    },
    candidate: {
      movement: num(candidate.policy_activity?.nonzero_movement_rate),
      near_ball: num(candidate.policy_activity?.near_ball_rate),
      progression: num(candidate.progression?.elite_share),
      territory: num(candidate.territory?.elite_half_rate),
    },
    safety_passed: safetyPassed,
    meaningful_improvement: meaningfulImprovement,
    promote_runtime_recovery: safetyPassed && meaningfulImprovement,
  };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.error(
      "Usage: node tools/compare_recovery_benchmark.js baseline.json candidate.json [output.json]",
    );
    process.exit(2);
  }
  const result = compare(read(args[0]), read(args[1]));
  const rendered = JSON.stringify(result, null, 2) + "\n";
  if (args[2]) fs.writeFileSync(args[2], rendered);
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
