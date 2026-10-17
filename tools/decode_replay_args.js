"use strict";

const DEFAULT_SAMPLE_EVERY_TICKS = 6;
const CANONICAL_POSITIVE_INTEGER = /^[1-9]\d*$/;

function parseSampleEveryTicks(rawValue) {
  if (rawValue === undefined) return DEFAULT_SAMPLE_EVERY_TICKS;

  if (
    typeof rawValue !== "string" ||
    !CANONICAL_POSITIVE_INTEGER.test(rawValue)
  ) {
    throw new TypeError("invalid_sample_every_ticks");
  }

  const value = Number(rawValue);
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new RangeError("invalid_sample_every_ticks");
  }

  return value;
}

module.exports = {
  DEFAULT_SAMPLE_EVERY_TICKS,
  parseSampleEveryTicks,
};
