# Canonical Data Model

## Match

- match_id
- replay_sha256
- source message/report IDs
- stadium/map
- duration
- score
- match quality tier
- quality/rejection reasons

## PlayerIdentity

- player_id
- observed display names
- identity confidence
- first/last seen

## PlayerAppearance

- match_id
- player_id
- team
- inferred role
- role confidence
- participation duration
- raw report stats
- derived metrics

## Frame

- match_id
- frame/timestamp
- ball position/velocity
- player positions/velocities
- player inputs/actions where available
- possession/context labels

## Event

Examples:

- touch
- kick
- possession change
- pass
- interception
- shot
- goal
- turnover
- derived tactical event

Each event keeps source frame ranges and detector version.

## MetricSnapshot

Versioned metric values for:

- match
- player appearance
- player career window
- team/lineup

## ModelRun

- model/version
- dataset manifest
- training config
- code commit
- seed
- artifacts
- evaluation result
- champion/challenger status
