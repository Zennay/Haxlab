#!/usr/bin/env node
"use strict";

const fs = require("fs");
const initAPI = require("node-haxball");

const API = initAPI();
const { Replay } = API;

function usage() {
  console.error(
    "Usage: node tools/extract_stadium_from_replay.js <replay.hbr2> <output.hbs>",
  );
  process.exit(2);
}

const replayPath = process.argv[2];
const outputPath = process.argv[3];
if (!replayPath || !outputPath) usage();

const data = Uint8Array.from(fs.readFileSync(replayPath));
let reader = null;
let settled = false;
let stadium = null;
let frameCaptured = null;
let timeoutHandle = null;

function capture() {
  if (stadium || !reader?.state?.exportStadium) return;
  try {
    const exported = reader.state.exportStadium();
    if (!exported) return;
    stadium = exported;
    frameCaptured = Number(reader.getCurrentFrameNo?.() || 0);
  } catch (_) {}
}

function finish(error = null) {
  if (settled) return;
  settled = true;
  if (timeoutHandle) {
    clearTimeout(timeoutHandle);
    timeoutHandle = null;
  }
  try {
    capture();
    reader?.destroy?.();
  } catch (_) {}

  if (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
  if (!stadium) {
    console.error("Could not export stadium from replay state.");
    process.exit(1);
  }

  fs.mkdirSync(require("path").dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(stadium, null, 2) + "\n");
  process.stdout.write(
    JSON.stringify({
      schema: "haxlab-replay-stadium-export-v1",
      sourceFile: replayPath,
      outputPath,
      stadiumName: stadium.name || null,
      width: stadium.width ?? null,
      height: stadium.height ?? null,
      frameCaptured,
    }) + "\n",
  );
}

const callbacks = {
  onGameStart: capture,
  onGameTick: () => {
    capture();
    if (stadium) finish();
  },
  onStadiumChange: capture,
};

const fastRAF = (callback) => setImmediate(() => callback(Date.now()));
const fastCancelRAF = (handle) => clearImmediate(handle);

try {
  reader = Replay.read(data, callbacks, {
    requestAnimationFrame: fastRAF,
    cancelAnimationFrame: fastCancelRAF,
  });
  reader.onEnd = () => finish();
  reader.setSpeed(100000);

  timeoutHandle = setTimeout(() => {
    if (!settled) finish(new Error("stadium_extract_timeout"));
  }, 30000);
} catch (error) {
  finish(error);
}
