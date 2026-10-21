"use strict";

// Parse caller-provided replay-player identity evidence without coercion.
// Fail closed before the replay is opened or a shard is created.
const CANONICAL_NONNEGATIVE_INTEGER = /^(0|[1-9]\d*)$/;

function invalid(reason) {
  const error = new TypeError(`invalid_selected_player_map:${reason}`);
  error.code = "INVALID_SELECTED_PLAYER_MAP";
  return error;
}

// JSON.parse silently keeps the LAST value of a repeated object key. Walk the
// already-syntax-checked top-level string map so repeated decoded keys (including
// escaped spelling such as "\\u0031" and "1") cannot change caller provenance.
function assertNoDuplicateSelectedPlayerKeys(raw) {
  let pos = 0;
  const skipWhitespace = () => {
    while (pos < raw.length) {
      const code = raw.charCodeAt(pos);
      if (code !== 32 && code !== 9 && code !== 10 && code !== 13) break;
      pos += 1;
    }
  };
  const readString = () => {
    const start = pos;
    if (raw[pos] !== '"') throw invalid("invalid_identity");
    pos += 1;
    while (pos < raw.length) {
      if (raw.charCodeAt(pos) === 92) {
        pos += 2;
      } else if (raw[pos] === '"') {
        pos += 1;
        return JSON.parse(raw.slice(start, pos));
      } else {
        pos += 1;
      }
    }
    throw invalid("invalid_json");
  };

  skipWhitespace();
  if (raw[pos++] !== "{") throw invalid("not_object");
  skipWhitespace();
  if (raw[pos] === "}") return;

  const seen = new Set();
  while (pos < raw.length) {
    const key = readString();
    if (seen.has(key)) throw invalid("duplicate_player_id");
    seen.add(key);
    skipWhitespace();
    if (raw[pos++] !== ":") throw invalid("invalid_json");
    skipWhitespace();
    // Non-string values are invalid anyway; refuse them before interpreting
    // nested JSON as another source of top-level selection identities.
    readString();
    skipWhitespace();
    if (raw[pos] === "}") return;
    if (raw[pos++] !== ",") throw invalid("invalid_json");
    skipWhitespace();
  }
  throw invalid("invalid_json");
}

function parseSelectedPlayerMapJson(rawValue) {
  if (typeof rawValue !== "string") throw invalid("not_json_text");

  let payload;
  try {
    payload = JSON.parse(rawValue);
  } catch (_) {
    throw invalid("invalid_json");
  }

  if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
    throw invalid("not_object");
  }

  assertNoDuplicateSelectedPlayerKeys(rawValue);
  const entries = Object.entries(payload);
  if (entries.length === 0) throw invalid("empty");

  const result = new Map();
  for (const [playerIdText, identity] of entries) {
    if (!CANONICAL_NONNEGATIVE_INTEGER.test(playerIdText)) {
      throw invalid("invalid_player_id");
    }
    const playerId = Number(playerIdText);
    if (!Number.isSafeInteger(playerId) || playerId < 0) {
      throw invalid("invalid_player_id");
    }
    if (typeof identity !== "string" || !identity.trim()) {
      throw invalid("invalid_identity");
    }
    result.set(playerId, identity);
  }
  return result;
}


/**
 * Sampling cadence is immutable shard provenance. parseInt("6garbage") and
 * parseInt("6.5") silently become 6, producing mislabeled dataset requests.
 * Require an exact positive, safe decimal integer before replay/file access.
 */
function parseSampleEveryTicks(rawValue) {
  if (rawValue === undefined) return 6;
  if (typeof rawValue !== "string" || !/^[1-9][0-9]*$/.test(rawValue)) {
    const error = new TypeError("invalid_sample_every_ticks");
    error.code = "INVALID_SAMPLE_EVERY_TICKS";
    throw error;
  }
  const parsed = Number(rawValue);
  if (!Number.isSafeInteger(parsed)) {
    const error = new TypeError("invalid_sample_every_ticks");
    error.code = "INVALID_SAMPLE_EVERY_TICKS";
    throw error;
  }
  return parsed;
}

module.exports = { parseSelectedPlayerMapJson, parseSampleEveryTicks };
