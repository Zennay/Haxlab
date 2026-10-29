#!/usr/bin/env node
"use strict";

// Read-only standalone verifier for an already created imitation shard.
// It never decodes, trains, overwrites, removes or re-creates an artifact.
const fs = require("fs");
const crypto = require("crypto");
const path = require("path");

const SIZE_LIMIT = 128 * 1024;
const CHUNK_SIZE = 1024 * 1024;
const HEX_SHA256 = /^[0-9a-f]{64}$/;
const expectedKeys = [
  "sourceReplaySha256",
  "selectedPlayerMapSha256",
  "shardCompressedSha256",
];

function refuse(reason) {
  throw new Error(reason);
}

function openRegularNoFollow(file) {
  // A trusted artifact must not be followed through a symlink or FIFO.
  if (typeof fs.constants.O_NOFOLLOW !== "number") refuse("nofollow_unavailable");
  const fd = fs.openSync(
    file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK,
  );
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) refuse("source_not_regular");
    return { fd, stat };
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }
}

function unchanged(before, after) {
  return before.dev === after.dev &&
    before.ino === after.ino &&
    before.size === after.size &&
    before.mtimeMs === after.mtimeMs &&
    before.ctimeMs === after.ctimeMs;
}

function digestFile(file) {
  const { fd, stat } = openRegularNoFollow(file);
  try {
    const hash = crypto.createHash("sha256");
    const chunk = Buffer.allocUnsafe(CHUNK_SIZE);
    let offset = 0;
    while (offset < stat.size) {
      const read = fs.readSync(
        fd, chunk, 0, Math.min(chunk.length, stat.size - offset), offset,
      );
      if (read <= 0) refuse("source_truncated_during_read");
      hash.update(chunk.subarray(0, read));
      offset += read;
    }
    if (!unchanged(stat, fs.fstatSync(fd))) refuse("source_changed_during_read");
    return { sha256: hash.digest("hex"), bytes: offset };
  } finally {
    fs.closeSync(fd);
  }
}

function loadReceipt(file) {
  const { fd, stat } = openRegularNoFollow(file);
  try {
    if (stat.size <= 0 || stat.size > SIZE_LIMIT) refuse("receipt_size_invalid");
    const buffer = Buffer.alloc(stat.size);
    let offset = 0;
    while (offset < buffer.length) {
      const n = fs.readSync(fd, buffer, offset, buffer.length - offset, offset);
      if (n <= 0) refuse("receipt_truncated_during_read");
      offset += n;
    }
    if (!unchanged(stat, fs.fstatSync(fd))) refuse("receipt_changed_during_read");
    let receipt;
    try { receipt = JSON.parse(buffer.toString("utf8")); }
    catch (_) { refuse("receipt_json_invalid"); }
    if (receipt === null || typeof receipt !== "object" || Array.isArray(receipt)
        || receipt.schema !== "haxlab-imitation-extract-summary-v2"
        || receipt.shardSchema !== "haxlab-imitation-shard-v2") {
      refuse("receipt_schema_invalid");
    }
    if (!expectedKeys.every(key => HEX_SHA256.test(receipt[key]))) {
      refuse("receipt_sha256_fields_invalid");
    }
    if (!Number.isSafeInteger(receipt.compressedBytes) || receipt.compressedBytes < 1
        || !Number.isSafeInteger(receipt.samples) || receipt.samples < 1
        || !Number.isSafeInteger(receipt.rowWidth) || receipt.rowWidth < 1
        || receipt.rowWidth > 256) {
      refuse("receipt_shape_invalid");
    }
    return receipt;
  } finally {
    fs.closeSync(fd);
  }
}

function main() {
  if (process.argv.length < 4 || process.argv.length > 6) {
    refuse("usage: verify_imitation_receipt.js <shard.f32.gz> <receipt.json> [replay.hbr2|-] [selectedPlayerMapJson]");
  }
  const [shardPath, receiptPath, replayPath, selectedMap] = process.argv.slice(2);
  if (path.resolve(shardPath) === path.resolve(receiptPath)) {
    refuse("shard_receipt_paths_must_differ");
  }
  const receipt = loadReceipt(receiptPath);
  const shard = digestFile(shardPath);
  if (shard.bytes !== receipt.compressedBytes ||
      shard.sha256 !== receipt.shardCompressedSha256) {
    refuse("shard_compressed_bytes_or_sha256_mismatch");
  }

  let replayVerified = false;
  if (replayPath && replayPath !== "-") {
    const replay = digestFile(replayPath);
    if (replay.sha256 !== receipt.sourceReplaySha256) {
      refuse("replay_source_sha256_mismatch");
    }
    replayVerified = true;
  }

  let selectionVerified = false;
  if (selectedMap !== undefined) {
    const sha = crypto.createHash("sha256").update(selectedMap, "utf8").digest("hex");
    if (sha !== receipt.selectedPlayerMapSha256) {
      refuse("selected_player_map_sha256_mismatch");
    }
    selectionVerified = true;
  }
  // Byte-identity checks are not proof of the authenticity of an untrusted
  // receipt or of dataset/model release eligibility.
  process.stdout.write(JSON.stringify({
    schema: "haxlab-imitation-provenance-verification-v1",
    shard_bytes_verified: true,
    shard_sha256_verified: true,
    original_replay_bytes_verified: replayVerified,
    original_selection_argument_verified: selectionVerified,
    no_model_release_authority: true,
  }) + "\n");
}

try { main(); }
catch (error) {
  console.error("imitation_verification_refused:" + String(error?.message || "invalid_input"));
  process.exitCode = 1;
}
