"use strict";

const CANONICAL_NONNEGATIVE_INTEGER = /^(0|[1-9]\d*)$/;

function invalid(reason) {
  const error = new TypeError(`invalid_selected_player_map:${reason}`);
  error.code = "INVALID_SELECTED_PLAYER_MAP";
  return error;
}

function parseSelectedPlayerMapJson(rawValue) {
  if (typeof rawValue !== "string") {
    throw invalid("not_json_text");
  }

  let payload;
  try {
    payload = JSON.parse(rawValue);
  } catch (_) {
    throw invalid("invalid_json");
  }

  if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
    throw invalid("not_object");
  }

  const entries = Object.entries(payload);
  if (entries.length === 0) {
    throw invalid("empty");
  }

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

module.exports = {
  parseSelectedPlayerMapJson,
};
