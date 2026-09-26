# Candidate D — Narrow Multisource Rollout Robustness

## Hypothesis

Candidate C showed that 0.10 normalized standard deviations of AM/ST train-only
state jitter increased activity too aggressively and reduced useful
progression/territory behavior.

Candidate D tests one narrower change only:

- AM/ST train-only sequence-consistent physical-feature jitter: **0.05σ**
- architecture: unchanged
- seed: 1337
- epochs: 3
- train / validation / imitation holdout indexes: identical to Candidate B/C

## Frozen promotion evidence

Before this model change, three new replay sources were frozen and all prior
train / validation / holdout and consumed evaluation sources were excluded.

The frozen suite contains:

- 3 independent source replays;
- 16 replay scenarios per source;
- mirrored red/blue evaluation;
- 96 total replay-seeded matches.

Frozen manifest SHA-256:
`541b635e34492e9409c5d0fbe2ffd6303c75a2cd10348aebee9e5df49669a609`

Source replay SHA-256 values:

1. `7d19cbff75c35c521a782b33045b3fc595526f15b9e5c006232ca16fdf10a604`
2. `2e3368ce47a49ef0d6ea875b78a5bf6338c62b8f1d8162c936ffda841e3b88ef`
3. `ca9a1568a8fc68ca306f79d56156aec837586fd1b052de287c43ce03b505a1f8`

## Promotion rule

No performance threshold is changed.

Each of the three independent replay sources must individually pass the
existing replay duel gate, including the existing 0.55 match-score threshold.
The multisource gate passes only when **all three** source gates pass.

Candidate D uses an isolated experiment registry. Passing the experiment cannot
rewrite production champion pointers.
