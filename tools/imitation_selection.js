"use strict";

function parseSelectedPlayerMapJson(rawValue) {
  if (typeof rawValue !== "string" || rawValue.trim() === "") {
    throw new TypeError("selectedPlayerMapJson must be a non-empty JSON object");
  }

  let payload;
  try {
    payload = JSON.parse(rawValue);
  } catch (error) {
    throw new TypeError("selectedPlayerMapJson must be valid JSON");
  }

  if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
    throw new TypeError("selectedPlayerMapJson must be a JSON object");
  }

  const entries = Object.entries(payload);
  if (entries.length === 0) {
    throw new RangeError("selectedPlayerMapJson must select at least one player");
  }

  const selected = new Map();
  for (const [rawPlayerId, identity] of entries) {
    if (!/^(0|[1-9]\d*)$/.test(rawPlayerId)) {
      throw new TypeError(
        "selectedPlayerMapJson player ids must be canonical non-negative integers",
      );
    }

    const playerId = Number(rawPlayerId);
    if (!Number.isSafeInteger(playerId)) {
      throw new RangeError(
        "selectedPlayerMapJson player id exceeds the safe integer range",
      );
    }

    if (typeof identity !== "string" || identity.trim() === "") {
      throw new TypeError(
        "selectedPlayerMapJson identities must be non-empty strings",
      );
    }

    selected.set(playerId, identity);
  }

  return selected;
}

module.exports = {
  parseSelectedPlayerMapJson,
};
