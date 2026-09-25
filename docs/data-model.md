# Canonical Data Model

HaxLab keeps immutable raw HBR2 files separate from versioned derived analysis.
A new feature extractor must use a new analysis version instead of silently
overwriting an older result.

## Replay source

### RawReplay

- `sha256` — stable content identity
- archive path
- compressed size
- first archived timestamp
- original source path(s)

Raw HBR2 files remain immutable.

### ReplayProbe

Lightweight format validation and inventory metadata:

- replay version
- total frames
- duration
- decompressed byte count
- probe status/error

## Versioned replay analysis

Every derived pass is keyed by:

- replay SHA-256
- analyzer version, for example `state-pass-v3` or `state-pass-v4`
- status
- output path
- sampled-state count
- reconstructed-frame count
- raw-event count
- player count
- error
- updated timestamp

Older analysis versions stay queryable for comparison and rollback.

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

Prefer stable replay auth identity when available.

Current derived representation:

- `authHash` — truncated SHA-256 of replay auth; raw auth is not exported
- normalized display-name fallback when auth is unavailable
- observed display names
- identity confidence
- first/last seen

Display names must not be assumed unique.

## PlayerAppearance

One player within one replay:

- replay/match ID
- player identity
- team
- inferred role
- role confidence
- participation duration
- position/heatmap summary
- action/touch metrics
- pressure metrics
- goal/assist evidence
- derived skill dimensions

## Sampled state

HaxLab does not dump every reconstructed frame as verbose JSON.

The replay engine reconstructs the full replay, while compact state summaries are
sampled (currently normally every 6 ticks, about 10 Hz):

- ball position and speed
- player positions
- nearest-player-to-ball evidence
- close-ball evidence
- heatmap bins

Full reconstructed-frame counts are stored separately so completeness can be
verified.

## Sparse event streams — schema v4

`state-pass-v4` adds compact sparse event arrays on top of full state
reconstruction.

### Touch

Current compact columns:

1. frame
2. player ID
3. team ID
4. ball X
5. ball Y
6. nearest-opponent distance
7. under-pressure flag
8. outcome code
9. next player ID
10. progression

Touch outcome codes:

- 0 unresolved
- 1 self-retouch
- 2 teammate
- 3 opponent
- 4 goal

Repeated collision callbacks are debounced into logical touches.

### Kick

Current compact columns:

1. frame
2. player ID
3. team ID
4. ball X
5. ball Y
6. nearest-opponent distance
7. under-pressure flag
8. outcome code
9. next player ID

### Goal

Current compact columns:

1. frame
2. scoring team
3. inferred scorer player ID
4. inferred assist player ID

Goal and assist attribution is replay-derived evidence and must stay labeled as
inferred rather than ground truth.

## Player touch-chain aggregates

Schema v4 currently derives, among other fields:

- logical touches
- self-retouches
- teammate touch transfers
- turnovers
- recoveries
- kick transfers
- pressure observations
- under-pressure touches
- retained/lost transitions under pressure
- touch progression
- inferred touch goals
- inferred touch assists

These feed player-performance features; they do not by themselves define skill.

## Skill estimate

Internal player skill remains a vector. Current dimensions include:

- retention
- progression
- creation
- finishing
- defending
- positioning
- pressure/recovery
- risk management

Each dimension stores:

- estimated mean
- uncertainty
- effective evidence weight

The public experimental rating is derived from these dimensions after role-aware
normalization and shrinkage. It must remain reconstructible from the underlying
evidence.

## Leaderboard snapshot

Machine-readable snapshots can be generated from a completed analysis pass and
stored under:

`/var/lib/haxlab/derived/leaderboards/<analysis-version>.json`

Snapshot metadata includes:

- schema identifier
- generation timestamp
- source analysis root
- minimum matches/minutes filters
- ranked player rows
- dimensions and uncertainty

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
