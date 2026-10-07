# Imitation extractor selected-player map integrity

The imitation extractor receives a JSON object that binds replay-local player IDs
to canonical selected-player identities. That mapping is part of shard provenance,
so it must be validated before replay reconstruction begins.

## Contract

- The payload is valid JSON whose top-level value is a non-empty object.
- Player-ID keys are canonical non-negative decimal integers representable as
  JavaScript safe integers.
- `0` is valid; other IDs must not contain leading zeroes.
- Signs, whitespace, fractions, exponent notation and unsafe integer magnitudes
  are rejected rather than coerced.
- Identity values must already be strings and must contain at least one
  non-whitespace character.
- Accepted identity strings are preserved exactly; the parser does not trim or
  stringify caller evidence.
- Validation is implemented in `tools/extract_imitation_args.js` so it can be
  regression-tested without replay fixtures or `node-haxball`.

The extractor previously used `Object.entries(JSON.parse(...))` followed by
`Number(playerId)` and `String(identity)`. That could normalize malformed
evidence and collapse distinct textual keys onto one numeric replay-player ID.
