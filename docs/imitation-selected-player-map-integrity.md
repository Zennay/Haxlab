# Replay imitation selection: exact input contract

The selected replay-player map is part of the derived imitation shard's provenance. The extractor now validates its JSON **before** reading the replay or creating an output shard.

Accepted input is a non-empty JSON object mapping canonical decimal, non-negative JavaScript-safe player IDs to non-empty string identities. Only the literal `0` can begin with zero; `01`, `+1`, `1.0`, `1e0`, negative IDs, whitespace and numbers outside the safe integer range are rejected. Object values must already be strings with at least one non-whitespace character.

Valid values are preserved byte-for-byte as JavaScript strings (including their surrounding spaces when present): they are not trimmed or coerced into strings. Validation rejects arrays, null, empty maps, malformed JSON and numeric/boolean/null identity values. The extractor's row sampling, timing and replay semantics are unchanged.

The reusable `tools/extract_imitation_args.js` parser is dependency-free; `tools/extract_imitation.js` uses the same parser before creating output. This source-only change does not authorize replay ingest, learning runs, training/model promotion, a VPS job or production release. Required existing exact-head CI and reviewer/owner gates remain in place.

Ported from older PR-less branch `feature/data-pipeline-imitation-player-map-integrity-20261007` onto fresh current-main branch without copying its QA/test artifacts, preserving issue #452 scope.
