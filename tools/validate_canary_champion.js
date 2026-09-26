"use strict";

const fs = require("fs");
const {
  compareReplay,
} = require("./validate_multi_replay_champion");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function mean(values) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values) {
  if (!values.length) return 0;
  const rows = [...values].sort((a, b) => a - b);
  const middle = Math.floor(rows.length / 2);
  if (rows.length % 2) return rows[middle];
  return (rows[middle - 1] + rows[middle]) / 2;
}

function validateCanary(rows) {
  if (!Array.isArray(rows) || rows.length < 8) {
    throw new Error("canary validation requires at least 8 replay rows");
  }

  const movement = rows.map((row) => row.deltas.movement);
  const progression = rows.map((row) => row.deltas.progression);
  const territory = rows.map((row) => row.deltas.territory);
  const nearBall = rows.map((row) => row.deltas.near_ball);
  const runtimeErrors = rows.reduce(
    (sum, row) => sum + Number(row.candidate.runtime_errors || 0),
    0,
  );
  const maxAssistRate = Math.max(
    ...rows.map((row) => Number(row.candidate.assist_rate || 0)),
  );
  const maxSideGap = Math.max(
    ...rows.map((row) => Number(row.candidate.side_gap || 0)),
  );

  const aggregate = {
    replay_count: rows.length,
    mean_movement_delta: mean(movement),
    median_movement_delta: median(movement),
    worst_movement_delta: Math.min(...movement),
    movement_positive_replays: movement.filter((value) => value >= 0.02).length,
    movement_nonnegative_replays: movement.filter((value) => value >= 0).length,
    mean_progression_delta: mean(progression),
    median_progression_delta: median(progression),
    worst_progression_delta: Math.min(...progression),
    progression_safe_replays: progression.filter((value) => value >= -0.03).length,
    mean_territory_delta: mean(territory),
    median_territory_delta: median(territory),
    worst_territory_delta: Math.min(...territory),
    mean_near_ball_delta: mean(nearBall),
    worst_near_ball_delta: Math.min(...nearBall),
    total_runtime_errors: runtimeErrors,
    max_assist_rate: maxAssistRate,
    max_side_gap: maxSideGap,
  };

  const atLeast80 = Math.ceil(rows.length * 0.8);
  const atLeast70 = Math.ceil(rows.length * 0.7);
  const checks = {
    enough_replays: rows.length >= 10,
    no_runtime_errors: runtimeErrors === 0,
    mean_movement_improves: aggregate.mean_movement_delta >= 0.03,
    median_movement_improves: aggregate.median_movement_delta >= 0.025,
    movement_generalizes:
      aggregate.movement_nonnegative_replays >= atLeast80,
    broad_material_movement_gain:
      aggregate.movement_positive_replays >= atLeast70,
    mean_progression_preserved:
      aggregate.mean_progression_delta >= -0.015,
    median_progression_preserved:
      aggregate.median_progression_delta >= -0.01,
    no_progression_collapse:
      aggregate.worst_progression_delta >= -0.08,
    progression_generalizes:
      aggregate.progression_safe_replays >= atLeast70,
    mean_territory_preserved:
      aggregate.mean_territory_delta >= -0.02,
    no_territory_collapse:
      aggregate.worst_territory_delta >= -0.08,
    mean_near_ball_preserved:
      aggregate.mean_near_ball_delta >= -0.001,
    assist_is_bounded: aggregate.max_assist_rate <= 0.02,
    side_gap_bounded: aggregate.max_side_gap <= 0.15,
  };

  return {
    schema: "haxlab-canary-champion-validation-v1",
    replay_results: rows,
    aggregate,
    checks,
    validated: Object.values(checks).every(Boolean),
  };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 2) {
    console.error(
      "Usage: node tools/validate_canary_champion.js manifest.json output.json",
    );
    process.exit(2);
  }

  const manifest = read(args[0]);
  const cases = Array.isArray(manifest.cases) ? manifest.cases : [];
  if (cases.length < 8) {
    throw new Error("canary manifest must contain at least 8 replay cases");
  }

  const rows = cases.map((entry, index) => {
    const label = String(entry.label || entry.sha256 || `replay-${index + 1}`);
    return compareReplay(
      label,
      read(entry.champion_path),
      read(entry.candidate_path),
    );
  });

  const result = {
    ...validateCanary(rows),
    candidate: manifest.candidate || null,
    source_set: manifest.source_set || null,
    canary_config: manifest.canary_config || null,
  };
  const rendered = JSON.stringify(result, null, 2) + "\n";
  fs.writeFileSync(args[1], rendered);
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

module.exports = { validateCanary };
