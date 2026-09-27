"use strict";

const fs = require("fs");
const { compare } = require("./compare_recovery_benchmark");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function main() {
  const args = process.argv.slice(2);
  if (args.length < 4 || args.length % 2 !== 0) {
    console.error(
      "Usage: node tools/select_recovery_config.js baseline.json " +
      "label1 candidate1.json [label2 candidate2.json ...] output.json",
    );
    process.exit(2);
  }

  const outputPath = args[args.length - 1];
  const baselinePath = args[0];
  const pairArgs = args.slice(1, -1);
  if (pairArgs.length % 2 !== 0) {
    throw new Error("candidate arguments must be label/path pairs");
  }

  const baseline = read(baselinePath);
  const rows = [];
  for (let i = 0; i < pairArgs.length; i += 2) {
    const label = pairArgs[i];
    const path = pairArgs[i + 1];
    const candidate = read(path);
    const result = compare(baseline, candidate);
    rows.push({
      label,
      path,
      recovery_config: candidate.recovery_config || null,
      ...result,
    });
  }

  const promotable = rows
    .filter((row) => row.promote_runtime_recovery)
    .sort((a, b) => {
      const ar = Number(a.recovery?.override_rate || 0);
      const br = Number(b.recovery?.override_rate || 0);
      if (ar !== br) return ar - br;
      const ap = Number(a.deltas?.progression_share || 0);
      const bp = Number(b.deltas?.progression_share || 0);
      if (ap !== bp) return bp - ap;
      return Number(b.deltas?.nonzero_movement_rate || 0) -
        Number(a.deltas?.nonzero_movement_rate || 0);
    });

  const selected = promotable[0] || null;
  const result = {
    schema: "haxlab-recovery-grid-v1",
    baseline_path: baselinePath,
    candidates: rows,
    selected: selected
      ? {
          label: selected.label,
          recovery_config: selected.recovery_config,
          recovery_override_rate: selected.recovery?.override_rate,
          deltas: selected.deltas,
        }
      : null,
    promote_recovery: Boolean(selected),
  };

  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + "\n");
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
}

if (require.main === module) {
  try { main(); }
  catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}
