# Autonomous Research Controller

The VPS should keep HaxLab progressing, but it should not confuse **activity** with **improvement**.

The controller therefore works as a state machine. It chooses the next useful stage based on durable evidence in the runtime ledger.

## Stages

```text
INGEST
  ↓
PARSE
  ↓
FEATURES
  ↓
QUALITY / SKILL REFRESH
  ↓
DATASET BUILD
  ↓
TRAIN CHALLENGER
  ↓
EVALUATE
  ↓
PROMOTE or REJECT
  ↓
FAILURE MINING
  ↓
CURRICULUM UPDATE
  └───────────────→ TRAIN CHALLENGER
```

The system may also return `IDLE` when there is not enough new evidence to justify expensive work.

## What "self-improving" means here

HaxLab is allowed to improve:

- replay filtering;
- dataset weighting;
- player/context estimates;
- curriculum composition;
- model parameters;
- training hyperparameters inside configured bounds;
- opponent populations;
- hard-case scenario selection.

It must **not** silently rewrite production code and deploy arbitrary changes to itself.

Code changes remain a separate software-development process. This keeps model improvement measurable and prevents a bad experiment from corrupting the system that judges it.

## Experiment memory

Every experiment should store:

- hypothesis / strategy;
- parent champion;
- dataset version;
- feature version;
- training configuration;
- random seeds;
- wall-clock/resource cost;
- evaluation outputs;
- regressions;
- promoted/rejected status.

Repeatedly losing configurations should be deprioritized. Promising configurations can spawn bounded variants.

## Experiment selection

Later versions can rank candidate experiments by an approximate value:

```text
expected information gain
× probability of useful improvement
÷ compute cost
```

Examples:

- compare Gold-only imitation vs Gold+Silver weighted imitation;
- test stronger turnover penalty;
- test curriculum oversampling of last-man-defence failures;
- test population mixture with older champions;
- compare role-aware vs role-agnostic observations.

The controller should prefer experiments that answer a useful question, not random hyperparameter churn.

## Bootstrap policy for ~6000 replays

For the first batch, the controller should prioritize data understanding over model training:

1. finish raw ingest/dedupe;
2. estimate corruption/parse failure rate;
3. inspect map/stadium distribution;
4. inspect player identity/name distribution;
5. calculate quality-tier distribution;
6. inspect score/duration/team balance distributions;
7. establish holdout split;
8. establish deterministic analytics baselines;
9. train first imitation baseline;
10. only then begin iterative experiments.

The first champion is simply a reproducible baseline. It does not need to be strong; it needs to make future progress measurable.

## New batches later

When more HBR2 files arrive:

- ingest only unseen content;
- update aggregates;
- keep frozen benchmark sets protected;
- create a new dataset version;
- estimate how much genuinely useful data was added;
- train again only if policy says the delta is meaningful;
- keep comparing to historical champions.

This allows the VPS to run continuously while staying conservative about expensive training.
