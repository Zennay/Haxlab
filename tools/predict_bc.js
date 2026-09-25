#!/usr/bin/env node
"use strict";

const fs = require("fs");

function usage() {
  console.error(
    "Usage: node tools/predict_bc.js <model.portable.json> <features.json>",
  );
  process.exit(2);
}

const modelPath = process.argv[2];
const featuresPath = process.argv[3];
if (!modelPath || !featuresPath) usage();

const model = JSON.parse(fs.readFileSync(modelPath, "utf8"));
const features = JSON.parse(fs.readFileSync(featuresPath, "utf8"));

if (model.schema !== "haxlab-bc-portable-v1") {
  throw new Error(`unsupported model schema: ${model.schema}`);
}

function dotRow(vector, matrix, column) {
  let total = 0;
  for (let i = 0; i < vector.length; i += 1) {
    total += Number(vector[i]) * Number(matrix[i][column]);
  }
  return total;
}

const inputColumns = model.input_columns || [];
const x = inputColumns.map((name, index) => {
  if (!Object.prototype.hasOwnProperty.call(features, name)) {
    throw new Error(`missing model feature: ${name}`);
  }
  const value = Number(features[name]);
  if (!Number.isFinite(value)) {
    throw new Error(`non-finite model feature: ${name}`);
  }
  return (value - Number(model.mean[index])) / Number(model.std[index]);
});

const hiddenDim = model.b1.length;
const hidden = new Array(hiddenDim);
for (let j = 0; j < hiddenDim; j += 1) {
  hidden[j] = Math.max(0, dotRow(x, model.w1, j) + Number(model.b1[j]));
}

const directionLogits = new Array(9);
for (let j = 0; j < 9; j += 1) {
  directionLogits[j] = dotRow(hidden, model.wd, j) + Number(model.bd[j]);
}
const maxLogit = Math.max(...directionLogits);
const exp = directionLogits.map((value) => Math.exp(value - maxLogit));
const denom = exp.reduce((sum, value) => sum + value, 0);
const directionProb = exp.map((value) => value / denom);
let directionClass = 0;
for (let i = 1; i < directionProb.length; i += 1) {
  if (directionProb[i] > directionProb[directionClass]) directionClass = i;
}

let kickLogit = Number(model.bk[0] || 0);
for (let i = 0; i < hidden.length; i += 1) {
  kickLogit += hidden[i] * Number(model.wk[i]);
}
kickLogit = Math.max(-30, Math.min(30, kickLogit));
const kickProbability = 1 / (1 + Math.exp(-kickLogit));
const kickThreshold = Number(model.kick_threshold ?? 0.5);

const direction = (model.direction_classes || []).find(
  (item) => Number(item.class_id) === directionClass,
);
if (!direction) throw new Error("missing direction class metadata");

process.stdout.write(
  JSON.stringify(
    {
      dir_x: Number(direction.dir_x),
      dir_y: Number(direction.dir_y),
      kick: kickProbability >= kickThreshold,
      direction_class: directionClass,
      direction_confidence: directionProb[directionClass],
      kick_probability: kickProbability,
      kick_threshold: kickThreshold,
    },
    null,
    2,
  ) + "\n",
);
