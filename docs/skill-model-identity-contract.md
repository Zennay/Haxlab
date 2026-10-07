# Skill model identity contract

Skill evidence is keyed by canonical player and role strings. The model boundary
must reject non-canonical whitespace instead of silently preserving aliases that
would split one player or role across multiple downstream keys.

## Contract

- `SkillObservation.player_id` is a native non-empty string with no surrounding whitespace.
- Optional `SkillObservation.role` is a native non-empty string with no surrounding whitespace.
- `PlayerSkillEstimate.player_id` follows the same rule and therefore rejects whitespace-only ids.
- Canonical values such as `auth:abc123`, `player-a`, and `midfield` are preserved byte-for-byte.

This is a fail-closed identity boundary. The model does not trim or normalize
malformed source evidence because doing so would erase evidence that the producer
published a non-canonical identity.

## Scope

This contract changes only the skill evidence model boundary and its focused
model regressions. It does not modify estimator math or priors, leaderboard
production/auditing, shards, ingestion, runtime, evaluation, or champion state.
