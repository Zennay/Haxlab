# Replay decoder sampling cadence: exact caller input

The replay decoder accepts an optional positional `sampleEveryTicks` argument. Because it changes the physics states captured and becomes part of published `simulation.sampleEveryTicks` provenance, an invalid explicit cadence must **never** silently become a plausible value.

- Omitting the argument selects the existing default **6**.
- An explicitly supplied value must be a canonical positive decimal JavaScript safe integer. `1` and `100` are valid.
- `0`, signs, surrounding whitespace, leading zeroes, fractional forms, exponent notation, trailing junk and values beyond `Number.MAX_SAFE_INTEGER` are rejected. The decoder prints `invalid_sample_every_ticks` and exits with usage rather than opening a replay.
- The accepted cadence is used unchanged and recorded unchanged in the output.

The lightweight `tools/decode_replay_args.js` parser does not import `node-haxball`; the actual decoder validates arguments before loading the replay engine. Event decoding and the rest of the model/replay pipeline are unchanged.

Integrated existing source from older draft PR #451 / issue #450 on a fresh current-main branch, without copying or creating QA/test files, running tests, changing worker allocation or affecting live runner/model state. Existing required CI/review and owner release gates remain applicable.
