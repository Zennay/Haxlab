# Candidate E — stronger future-direction auxiliary objective

## Frozen hypothesis

Candidate E is a one-factor follow-up to Candidate D.

Everything from Candidate D remains frozen except the weight of the existing
future-direction auxiliary loss:

- architecture: unchanged;
- train / validation / frozen human holdout: identical to Candidate D;
- seed: `1337`;
- epochs: `3`;
- window: `8`;
- future horizon: `5` steps;
- AM/ST train-only sequence-consistent state jitter: `0.05σ`;
- learning rate: `0.0008`;
- **future loss weight: `0.50` instead of `0.35`**.

No second training factor may change in this experiment.

## Why this factor

Candidate D became the strongest offline imitation candidate so far, but its
closed-loop behavior remained poor. Arena v2 and the private human canary both
showed that the policy still produced short-horizon / wrong-direction activity,
team-shape collapse, boundary drift and weak context adaptation.

The Candidate E hypothesis is therefore deliberately narrower than "more
movement": a moderately stronger future-direction auxiliary gradient should
bias the shared representation toward trajectory-aware action selection while
retaining Candidate D's narrower 0.05σ AM/ST neighborhood augmentation.

## Frozen evaluation evidence

Candidate E is bound before training to:

- VPS root:
  `/var/lib/haxlab/derived/evaluation/promotion-v2-frozen-suite-v2`
- manifest SHA-256:
  `fc255d642995638c4d1cc23a709da660c6c0ed3c43ed77a20386f4134415d315`
- three frozen replay sources;
- 16 scenarios per source;
- mirrored red/blue replay-duel evaluation;
- calibrated 30-second Closed-Loop Arena v2 evaluation.

The suite may not be regenerated, replaced or cherry-picked after Candidate E
results exist.

## Gates

Candidate E is experimental and isolated from production pointers.

Required evidence:

1. verify exact train, validation and frozen human-holdout index hashes;
2. verify the exact promotion-v2 suite manifest and every per-source artifact
   hash before training;
3. train one model with the frozen configuration above;
4. report offline and per-role metrics;
5. run the existing scripted sandbox gate;
6. run all three frozen replay-seeded source duels and aggregate them with the
   existing multisource gate;
7. run calibrated Arena v2 on all three frozen sources using 16 scenarios,
   30-second episodes, squad mode and GK/DM/AM/ST plug-and-play mode;
8. keep all existing thresholds unchanged;
9. treat any failing source or role as a rejection;
10. do not update production `current.json` or `live.json`.

A later private live canary is only allowed after every automated gate passes.
