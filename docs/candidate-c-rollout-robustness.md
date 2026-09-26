# Candidate C — Rollout Robustness

## Hypothesis

Candidate B's offline imitation metrics are strong, but replay-seeded loss
scenarios show a closed-loop failure mode concentrated in AM/ST behavior.
Training labels are not stationary-dominated, so direction-class reweighting is
not justified.

Candidate C tests one isolated change: modest train-only state-neighbourhood
augmentation for AM/ST.

## Controlled change

- Base architecture: unchanged elite temporal MLP.
- Train / validation / frozen imitation holdout: exact Candidate-B shard indexes.
- Seed: 1337.
- Epochs: 3.
- State jitter: 0.10 normalized standard deviations.
- Roles receiving jitter: AM and ST only.
- Jitter mode: one physical-feature offset per temporal sequence, held constant
  across all 8 history frames.
- Presence flags, score difference, labels, validation and holdout inputs are
  never jittered.

## Frozen training inputs

- train index SHA-256:
  `920ca18fdbc3682130307c01f91d61ad745d6642824f19962c6a2c614ffe1923`
- validation index SHA-256:
  `baaa00830ec3906640e893344262282d931de15b7a1c5c63485df1da44d6ba27`
- frozen imitation holdout index SHA-256:
  `a80b9789547ba3befd993bfd4c62c84b2c2c999a6d9283d9b2ef3c08451083fe`

## Frozen champion evaluation

Candidate-B's old replay suite was used for failure analysis and is therefore
treated as development evidence from this point onward.

Candidate C uses a newly frozen, disjoint replay source:

- replay SHA-256:
  `7e98b5002160940e3f53e7a3ca9108c85030baf23e488e8ca572f0a2ec187e7f`
- excluded prior train / validation / holdout / old evaluation replays: 1,143
- scenarios: 16, mirrored to 32 evaluation matches
- frozen manifest SHA-256:
  `1d73bc436cda03a1d9f5db0f2c36479c415d5074468b45c5f188097b6157b3e1`

The workflow verifies all frozen hashes before training.

## Promotion rule

No threshold is changed for Candidate C. The current champion remains in place
unless the unchanged offline, sandbox and replay-seeded champion gates all pass.
The Candidate-C registry is isolated from production; passing the experiment
does not itself rewrite the production live pointer.
