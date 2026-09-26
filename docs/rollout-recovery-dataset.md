# Rollout / Recovery Dataset v1

## Purpose

Arena v2 exposes states that the behavioral-cloning policy visits under its own
closed-loop control. Those states are development evidence for recovery and
trajectory learning; they are **not** human demonstrations and **not** unseen
promotion evidence.

The pipeline has two deliberately separate layers:

1. `elite_closed_loop_arena_v2.js` can optionally emit a sparse JSONL failure
   trace for the challenger policy.
2. `python -m haxlab.learning.recovery_dataset` turns one or more immutable
   traces into a deterministic, deduplicated recovery dataset.

With tracing disabled, Arena v2 keeps its existing evaluation behavior and
output semantics.

## Sparse failure trace

Enable tracing with:

```bash
node tools/elite_closed_loop_arena_v2.js ... \
  --recovery-trace-output /path/source-01.jsonl \
  --source-ref <git-sha> \
  --source-replay-sha256 <64-char-replay-sha256>
```

The trace header freezes the challenger/champion/partner model SHA-256 values,
stadium SHA-256, scenario SHA-256, the exact source replay SHA-256, Arena config,
seed and source ref. Recovery tracing fails closed if the source replay SHA is
missing or malformed.

Only challenger states are mined. Candidate/reference comparisons and promotion
thresholds are unchanged.

A bounded sample is retained when one or more failure families fire:

- field-boundary drift;
- large role deviation (>= 450 arena units from the deterministic role target);
- far-from-ball stationary behavior;
- missed action adaptation after material ball-context change;
- OOD z-score above the existing Arena diagnostic threshold;
- full-team formation collapse or overstretch.

Each row carries the current model-visited features/action plus a short causal
history window. Where defensible, diagnostics also expose deterministic
auxiliary targets: role target and team-shape state. These targets are evidence
for later training design; they do not claim that the safety controller is
ground-truth expert play.

Repeated failures are rate-limited per role/match so a long boundary or stall
episode cannot dominate the dataset.

## Deterministic recovery dataset

Build a versioned dataset with:

```bash
PYTHONPATH=src python -m haxlab.learning.recovery_dataset \
  source-01.jsonl source-02.jsonl source-03.jsonl \
  --output-root /var/lib/haxlab/derived/training/recovery \
  --dataset-id candidate-e-arena-v2-v1 \
  --forbidden-source-sha256-file /path/promotion-reserved-sha256.txt
```

The builder validates the trace schema and records every input trace hash plus
source-replay/model/scenario/stadium/seed provenance. State fingerprints still
quantize the current role/features/player/ball state to 3 decimals for global
dedupe, but **split assignment is no longer state-level**.

Each replay-seeded Arena rollout gets a stable rollout-group fingerprint from
the source replay, challenger, scenario/stadium hashes, seed, scenario index,
mode, tested role, side and repeat. The whole rollout inherits one split:

- train: rollout-group hash bucket 0-79;
- validation: 80-89;
- evaluation: 90-99.

If an identical state appears in rollout groups assigned to different splits,
only the deterministic canonical-owner observation is emitted; cross-split
duplicate observations are audit-counted and dropped rather than leaking into
another split.

`--forbidden-source-sha256-file` provides a fail-closed reserve list for unseen
promotion sources. Any recovery trace sourced from a replay in that list is
rejected before dataset construction.

Outputs are `train.jsonl`, `validation.jsonl`, `evaluation.jsonl` and
`manifest.json`; the manifest stores SHA-256 for every output. Dataset IDs are
immutable: an exact byte-identical rebuild is idempotent, but reusing an
existing ID with different content raises an error instead of overwriting it.

## Data-separation invariant

`human_replay_data_included` is always `false`. Recovery datasets live in their
own derived-learning namespace and never rewrite human train/validation/holdout
indexes.

Once a replay/scenario has been used to mine recovery data, it is development
evidence. It must not later be presented as pristine unseen promotion evidence
for the challenger trained from that recovery data. A fresh promotion suite
must be frozen before that challenger is trained.

## Promotion invariant

Recovery evidence can motivate or train a challenger, but cannot promote one.
The unchanged Promotion v2 contract still requires frozen human holdout,
replay-seeded behavior, calibrated Arena v2 on fresh unseen sources,
guard-independence and a private canary before any live pointer may change.
