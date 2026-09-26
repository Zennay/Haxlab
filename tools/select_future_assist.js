"use strict";

const fs = require("fs");
const { compare } = require("./compare_future_challenger");

function read(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function main() {
  const args = process.argv.slice(2);
  if (args.length < 8 || args.length % 2 !== 0) {
    console.error(
      "Usage: node tools/select_future_assist.js " +
      "champion-offline.json challenger-offline.json champion-loop.json plain-loop.json " +
      "label1 assisted1.json [label2 assisted2.json ...] output.json",
    );
    process.exit(2);
  }

  const championOffline = read(args[0]);
  const challengerOffline = read(args[1]);
  const championLoop = read(args[2]);
  const plainLoop = read(args[3]);
  const outputPath = args[args.length - 1];
  const pairArgs = args.slice(4, -1);
  if (pairArgs.length % 2 !== 0) {
    throw new Error("assisted candidates must be label/path pairs");
  }

  const rows = [];
  for (let i = 0; i < pairArgs.length; i += 2) {
    const label = pairArgs[i];
    const assistedPath = pairArgs[i + 1];
    const assisted = read(assistedPath);
    const result = compare(
      championOffline,
      challengerOffline,
      championLoop,
      plainLoop,
      assisted,
    );
    rows.push({
      label,
      path: assistedPath,
      future_assist_config: assisted.future_assist_config || null,
      ...result,
    });
  }

  const promotable = rows
    .filter((row) => row.promote_future_challenger)
    .sort((a, b) => {
      const ar = Number(a.closed_loop?.future_assist_rate || 0);
      const br = Number(b.closed_loop?.future_assist_rate || 0);
      if (ar !== br) return ar - br;

      const ap = Number(a.deltas?.future_progression || 0);
      const bp = Number(b.deltas?.future_progression || 0);
      if (ap !== bp) return bp - ap;

      const am = Number(a.deltas?.future_movement || 0);
      const bm = Number(b.deltas?.future_movement || 0);
      return bm - am;
    });

  const selected = promotable[0] || null;
  const result = {
    schema: "haxlab-future-assist-sweep-v1",
    candidates: rows,
    selected: selected
      ? {
          label: selected.label,
          future_assist_config: selected.future_assist_config,
          assist_rate: selected.closed_loop?.future_assist_rate,
          deltas: selected.deltas,
          checks: selected.checks,
        }
      : null,
    promote_future_challenger: Boolean(selected),
  };

  const rendered = JSON.stringify(result, null, 2) + "\n";
  fs.writeFileSync(outputPath, rendered);
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
