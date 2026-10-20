"use strict";

const DEFAULT_SAMPLE_EVERY_TICKS = 6;

function parseSampleEveryTicks(rawValue) {
  if (rawValue == null) return DEFAULT_SAMPLE_EVERY_TICKS;
  if (typeof rawValue !== "string" || !/^[1-9]\d*$/.test(rawValue)) {
    throw new TypeError("sampleEveryTicks must be a canonical positive integer");
  }

  const parsed = Number(rawValue);
  if (!Number.isSafeInteger(parsed)) {
    throw new RangeError("sampleEveryTicks exceeds the safe integer range");
  }
  return parsed;
}

module.exports = {
  DEFAULT_SAMPLE_EVERY_TICKS,
  parseSampleEveryTicks,
};
