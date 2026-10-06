# Evaluation Arena

## Purpose

Prevent apparent progress from replacing real progress.

Offline imitation accuracy is bootstrap evidence only. A challenger is promotion-eligible only after reproducible frozen-suite and closed-loop evidence shows that it can remain stable, useful and role-correct while its own actions change future states.

## Test categories

A challenger should be tested against:

- previous champion;
- multiple older champions;
- fixed scripted baselines;
- population opponents;
- frozen scenario suites;
- human holdout situations never used for training;
- replay-seeded closed-loop episodes;
- plug-and-play lineups with fixed partner models;
- perturbation and recovery cases;
- guard-independence conditions.

## Control variables

Where applicable, rotate:

- team side;
- start position;
- random seed;
- role;
- score state;
- time remaining;
- opponent style.

Candidate and reference runs must use the same frozen scenario coordinates, side, repeat index, runtime configuration and partner lineup whenever they are compared as a pair.

## Metrics

At minimum retain:

- win/loss/draw;
- goal differential;
- scoring and conceding rates;
- possession/territory;
- turnover rate;
- scenario success;
- stability/error rate;
- ball involvement;
- role-zone deviation;
- held-action/stall behavior;
- OOD/runaway events;
- guard/recovery intervention rate.

Use confidence intervals rather than one small batch of games.

## Regression gate

Promotion must fail if a challenger shows a severe regression in an important frozen scenario even when aggregate win rate rises.

Malformed, incomplete, internally inconsistent or coercible evidence must fail closed rather than being repaired implicitly by the promotion consumer.

## Exact-source evidence contract

Every promotion-relevant result must be bound to immutable inputs. At minimum retain and validate:

- exact 40-character code commit SHA;
- challenger, champion and partner model identities and hashes;
- frozen scenario-suite identity/hash;
- stadium/physics/runtime configuration;
- seeds, scenario/side/repeat coordinates and sampling cadence;
- policy/gate version and thresholds;
- raw result rows plus derived summaries;
- the audit artifact produced by the exact run.

A green workflow is not promotion evidence unless checkout provenance proves the tested source SHA. Artifact collection is part of the gate: a run that cannot publish its required audit evidence is incomplete.

## Paired closed-loop integrity

Closed-loop comparison must preserve the experimental pair:

- candidate and reference rows use the same scenario, side and repeat coordinate;
- plug-and-play comparisons use the same non-tested partner models;
- raw W/D/L, scores and per-role summaries reconcile to the underlying match rows;
- runtime/config metadata matches the frozen calibration contract;
- calibrated gates are evaluated only on evidence produced with the calibrated rollout duration, sample cadence and tie margin.

Safety guards may protect the environment, but their interventions count as failure evidence. A policy that appears stable only because role leashes or recovery guards rescue it does not pass the guard-independence gate.

## Reproducibility

Every evaluation record stores:

- challenger artifact/version;
- opponent versions;
- code commit;
- physics/environment version;
- config;
- seeds;
- dataset IDs;
- results.

Same-SHA result reuse is allowed only when all bound model, scenario, stadium and runtime inputs still match. Missing, unreadable or changed inputs invalidate reuse.

Workflow timeout, cancellation or infrastructure interruption means **incomplete evidence**. It is neither a promotion pass nor a model-quality rejection unless the completed evidence itself proves a gate failure.

## Promotion sequence

The conservative promotion sequence is:

1. frozen human/offline evidence passes;
2. frozen multisource suite is structurally valid and immutable;
3. closed-loop paired calibration/evaluation completes;
4. guard-independence and regression gates pass;
5. exact-source artifacts are retained and promotion consumers validate them fail-closed;
6. champion pointer changes only after all mandatory gates are green;
7. live canary comes after automated promotion evidence, not before it.

No single successful workflow, fresher model, or higher offline accuracy is sufficient on its own.

## Champion promotion

Promotion policy is machine-readable and must remain explainable through human-readable evidence.

The implementation is deliberately conservative: incomplete or ambiguous evidence leaves the current champion unchanged.
