# Frozen promotion metamorphic acceptance contract

This lane protects the conservative `haxlab.evaluation.promotion.decide_promotion`
decision without changing its thresholds, evidence schema, implementation or
the champion pointer.

## Scope

The focused suite `tests/test_evaluation_promotion_metamorphic_contract.py`
uses in-memory `EvaluationEvidence`, `Regression` and `PromotionPolicy`.
It never reads training data, performs a match, opens sealed holdout evidence,
writes to VPS runtime storage or promotes a champion.

The suite starts from a valid challenger at the default inclusive acceptance
boundaries: **500 games**, **0.51 lower confidence bound** (at the policy's
inclusive 0.51 minimum), **98/100 frozen scenarios**, distinct model identities and a
real Boolean reproducibility witness.

It verifies the following independent metamorphic properties, and also runs a
108-case Cartesian product of games (499/500/501), lower bounds
(0.49/0.51/0.52), frozen successes (97/98/100), critical-regression presence
and Boolean reproducibility. The expected eligibility is an independently
specified logical AND of the five gates, not a call to the implementation.

It verifies these metamorphic properties:

1. Repeating the decision on unchanged evidence/policy is deterministic and
   does not mutate either input.
2. Reducing games, confidence, scenario passes, or reproducibility cannot
   turn an accepted candidate into an accepted weaker candidate.
3. A more restrictive policy cannot let the same candidate bypass a gate.
4. Even a near-perfect aggregate challenger remains rejected with a critical
   frozen-scenario regression under the default policy.
5. Only an explicit native Boolean `True` policy override can allow critical
   regressions. This is an observation of the current conservative API, **not**
   authorization to change the live promotion policy.
6. Noncritical warning regressions and a finite negative goal difference are
   diagnostic, not undocumented vetoes.

These tests are **contract regressions**, not a simulation win-rate claim,
not calibration, not fresh multisource evidence, and not a human holdout.
They cannot alone establish Arena-v2 acceptance.

## Run

With the repository source and test dependencies installed:

```bash
python -m pytest -q tests/test_evaluation_promotion_metamorphic_contract.py
```

The isolated proof workflow
`.github/workflows/evaluation-promotion-metamorphic-proof.yml` is staged
on this branch. Before default-branch registration, GitHub may not expose
`workflow_dispatch` for a new workflow. A narrowly scoped **push trigger**
provides an alternative without creating a PR: when runner sequencing permits,
deliberately add or change
`.github/haxlab-evaluation-promotion-metamorphic-proof.trigger` on
`validation/evaluation-promotion-metamorphic-20261009`. Ordinary test,
documentation and workflow commits do not match this trigger path.
The trigger file does not exist in the staged candidate and **no run has been
requested**. The proof uses the triggering commit as the exact source SHA,
checks the live branch tip and the expected VPS/runner identity, and does not
persist checkout credentials. It cannot run via PR events or schedules.
Do not trigger it while the shared HaxLab runner is reserved for canonical
#100 and #436 validation.

## Integration/ownership

This candidate is based on frozen Arena-v2
`c0ec9ef22a54e1d65a9160aba295589fcf8f6b81`, not moving `main`.
Only three additive paths are in scope (test, this documentation and dedicated
manual proof). Do not modify or merge owner-controlled calibration/multisource
workflows, evidence readers or product gate logic from this lane.

Before integration, a reviewer must reconcile canonical branch drift, check
disjoint file ownership, obtain exact-head focused green test evidence and
the required repository checks. No evidence in this lane authorizes model
promotion.
