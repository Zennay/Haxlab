"use strict";

const fs = require("fs");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function metric(obj, ...path) {
  let value = obj;
  for (const key of path) value = value?.[key];
  return num(value);
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

function compareReplay(label, champion, candidate) {
  const championMovement = metric(
    champion,
    "policy_activity",
    "nonzero_movement_rate",
  );
  const candidateMovement = metric(
    candidate,
    "policy_activity",
    "nonzero_movement_rate",
  );
  const championProgression = metric(champion, "progression", "elite_share");
  const candidateProgression = metric(candidate, "progression", "elite_share");
  const championTerritory = metric(
    champion,
    "territory",
    "elite_half_rate",
  );
  const candidateTerritory = metric(
    candidate,
    "territory",
    "elite_half_rate",
  );
  const championNearBall = metric(
    champion,
    "policy_activity",
    "near_ball_rate",
  );
  const candidateNearBall = metric(
    candidate,
    "policy_activity",
    "near_ball_rate",
  );

  return {
    label,
    champion: {
      movement: championMovement,
      progression: championProgression,
      territory: championTerritory,
      near_ball: championNearBall,
    },
    candidate: {
      movement: candidateMovement,
      progression: candidateProgression,
      territory: candidateTerritory,
      near_ball: candidateNearBall,
      runtime_errors: metric(candidate, "policy_activity", "runtime_errors"),
      assist_rate: metric(candidate, "policy_activity", "future_assist_rate"),
      side_gap: metric(candidate, "paired_side_gap", "territory_abs"),
    },
    deltas: {
      movement: candidateMovement - championMovement,
      progression: candidateProgression - championProgression,
      territory: candidateTerritory - championTerritory,
      near_ball: candidateNearBall - championNearBall,
    },
  };
}

function validate(rows) {
  if (!Array.isArray(rows) || rows.length < 3) {
    throw new Error("multi-replay validation requires at least 3 replay rows");
  }

  const movement = rows.map((row) => row.deltas.movement);
  const progression = rows.map((row) => row.deltas.progression);
  const territory = rows.map((row) => row.deltas.territory);
  const nearBall = rows.map((row) => row.deltas.near_ball);
  const runtimeErrors = rows.reduce(
    (sum, row) => sum + row.candidate.runtime_errors,
    0,
  );
  const maxAssistRate = Math.max(
    ...rows.map((row) => row.candidate.assist_rate),
  );
  const maxSideGap = Math.max(...rows.map((row) => row.candidate.side_gap));

  const aggregate = {
    replay_count: rows.length,
    mean_movement_delta: mean(movement),
    median_movement_delta: median(movement),
    worst_movement_delta: Math.min(...movement),
    movement_nonnegative_replays: movement.filter((value) => value >= 0).length,
    mean_progression_delta: mean(progression),
    median_progression_delta: median(progression),
    worst_progression_delta: Math.min(...progression),
    mean_territory_delta: mean(territory),
    worst_territory_delta: Math.min(...territory),
    mean_near_ball_delta: mean(nearBall),
    worst_near_ball_delta: Math.min(...nearBall),
    total_runtime_errors: runtimeErrors,
    max_assist_rate: maxAssistRate,
    max_side_gap: maxSideGap,
  };

  const minimumNonnegative = Math.ceil(rows.length * 0.6);
  const checks = {
    no_runtime_errors: runtimeErrors === 0,
    mean_movement_improves: aggregate.mean_movement_delta >= 0.02,
    movement_generalizes:
      aggregate.movement_nonnegative_replays >= minimumNonnegative,
    mean_progression_preserved:
      aggregate.mean_progression_delta >= -0.02,
    no_progression_collapse:
      aggregate.worst_progression_delta >= -0.08,
    mean_territory_preserved:
      aggregate.mean_territory_delta >= -0.03,
    no_territory_collapse:
      aggregate.worst_territory_delta >= -0.08,
    mean_near_ball_preserved:
      aggregate.mean_near_ball_delta >= -0.001,
    assist_is_bounded: aggregate.max_assist_rate <= 0.20,
    side_gap_bounded: aggregate.max_side_gap <= 0.15,
  };

  return {
    schema: "haxlab-multi-replay-champion-validation-v1",
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
      "Usage: node tools/validate_multi_replay_champion.js manifest.json output.json",
    );
    process.exit(2);
  }

  const manifest = read(args[0]);
  const cases = Array.isArray(manifest.cases) ? manifest.cases : [];
  if (cases.length < 3) {
    throw new Error("manifest must contain at least 3 replay cases");
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
    ...validate(rows),
    candidate: manifest.candidate || null,
    source_set: manifest.source_set || null,
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

module.exports = { compareReplay, validate };
