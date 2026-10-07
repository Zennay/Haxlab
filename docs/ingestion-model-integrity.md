# Canonical ingestion model integrity

This contract defines the fail-closed boundary for the shared ingestion dataclasses in `haxlab.models`.

## Scope

The contract applies only to the canonical import datamodel:

- `ReplayFile`
- `AttachmentRef`
- `MatchReport`
- `MatchCandidate`
- `ImportFailure`
- `ImportManifest`

It does not change ingestion discovery, Discord parsing, replay/report matching, M0 publication, runtime state, evaluation, training selection, or champion state.

## Contract

Producer outputs must already be structurally valid when they cross the model boundary. The models do not coerce strings, booleans, floats, or other lookalike values into canonical evidence.

### Replay evidence

- replay SHA-256 is exactly 64 lowercase hexadecimal characters;
- replay byte size is a native non-negative integer;
- replay path and file name are native non-empty strings.

### Discord report evidence

- message identity is a native non-empty string without surrounding whitespace;
- optional timestamp, channel and report IDs use native strings when present;
- attachments are an immutable tuple of `AttachmentRef` values;
- attachment size is a native non-negative integer when present;
- scores are native non-negative integers when present, and red/blue scores are both present or both absent;
- possession is finite and within 0..100 when present, and red/blue possession values are both present or both absent;
- possession values are not required to sum exactly to 100, so the model does not invent precision beyond the source evidence.

### Match evidence

- replay identity is a canonical lowercase SHA-256;
- confidence is a native finite number in 0..1;
- reason evidence is an immutable tuple of canonical, non-empty, unique strings.

### Manifest evidence

- schema version is exactly native integer `1`;
- all counters are native non-negative integers;
- `replay_count == unique_replay_count + duplicate_replay_count`;
- match count cannot exceed unique replay count or report count;
- unmatched replay/report evidence is a list of canonical, non-empty, unique strings;
- failures are real `ImportFailure` objects whose source, stage and error remain valid.

`ImportManifest.as_dict()` revalidates the mutable manifest and every mutable failure before serialization. This prevents post-construction mutation from publishing malformed evidence.

## Failure semantics

Type drift raises `TypeError`. Semantically invalid values raise `ValueError`. Invalid values are rejected at the model boundary; they are never normalized into apparently valid evidence.

## Compatibility rule

Existing valid producers remain authoritative for constructing these models. This lane intentionally does not edit producer code. Focused regression coverage calls the current report parser and matcher and requires their normal output to satisfy this contract.

## Integration gate

The branch may be integrated only after exact-head HaxLab CI is green and current-main ownership is rechecked. No model, threshold, raw replay, M0 generation, evaluation, or champion pointer is mutated by this contract.
