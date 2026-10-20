# Replay decoder sampling cadence integrity

The replay decoder accepts an optional positional `sampleEveryTicks` value. Because
that value is emitted into derived replay evidence and controls which physics
states are sampled, the decoder must not silently normalize malformed input.

## Contract

- Omitting `sampleEveryTicks` uses the canonical default `6`.
- An explicit value must be canonical unsigned decimal text for a positive
  JavaScript safe integer.
- Leading zeroes, signs, surrounding whitespace, fractional values, exponent
  notation, trailing junk, zero, negatives and values above
  `Number.MAX_SAFE_INTEGER` are rejected.
- The accepted integer is used unchanged by the decoder and is written unchanged
  to `simulation.sampleEveryTicks`.
- Validation lives in `tools/decode_replay_args.js` so it can be regression-tested
  without replay fixtures or the `node-haxball` runtime dependency.

This closes the previous behavior where `parseInt`, fallback-to-six and
`Math.max(1, ...)` could turn malformed configuration into plausible derived
artifacts.
