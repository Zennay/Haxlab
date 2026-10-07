# Promotion authorization contract

## Purpose

The Arena v2 promotion path has two independent facts that must never be conflated:

1. the promotion policy evaluated the challenger evidence and returned a typed `PromotionDecision`;
2. the mandatory calibration, HaxLab CI, and multisource workflows all completed successfully on the same immutable commit SHA.

`haxlab.evaluation.promotion_authorization` is a downstream fail-closed boundary that binds those facts into one deterministic receipt. A positive promotion decision cannot become `authorized: true` unless the supplied mandatory-gate receipt is revalidated against the exact same commit.

This contract does not mutate models, thresholds, champion pointers, source suites, calibration state, or workflow state.

## Inputs

`authorize_promotion()` requires:

- an exact lowercase 40-character commit SHA;
- distinct non-empty candidate and champion identifiers;
- a typed `PromotionDecision`;
- an `haxlab-evaluation-gate-run-receipt-v1` object;
- the SHA-256 of the evaluated evidence;
- the SHA-256 of the policy/config used for the decision.

The gate receipt is not trusted merely because it says `mandatory_gates_green: true`. The consumer independently checks the receipt schema, exact head, complete mandatory gate set, workflow paths and names, terminal-success state, supported triggering event, positive run id/attempt, unique run ids, and canonical GitHub run URLs.

## Mandatory gates

The authorization boundary currently requires exactly:

- `Closed-Loop Arena v2 Frozen Policy Validation`;
- `HaxLab CI`;
- `Freeze Multisource Evaluation Suite v2 Current-Main`.

Every row must be bound to the same `exact_head` supplied to the promotion authorization request.

## Output

The deterministic JSON-compatible result uses schema `haxlab-promotion-authorization-v1` and scope `promotion-authorization-only`.

`authorized: true` means only:

- the supplied typed promotion decision passed; and
- the mandatory workflow evidence supplied to this function is terminal-success evidence for that same immutable head.

A rejected promotion decision always remains unauthorized even when all mandatory workflows are green.

## Explicit non-goals

This receipt is **not merge authorization**. It does not prove that the evaluation branch is current with the latest `main`, that a later resync has not changed evaluation-owned dependencies, or that post-resync mandatory gates have been rerun.

For PR #19 the integration sequence therefore remains:

1. finish the immutable calibration gate;
2. repair the canonical CI blocker in its owning lane and obtain fresh exact-head CI;
3. reconcile current-main drift through the existing resync ownership lane;
4. rerun all mandatory gates on the resynced exact SHA;
5. only then consume the promotion authorization receipt as one input to the final integration/promotion decision.

Historical green evidence from a different SHA never authorizes a later head.

## Integration boundary

This lane is intentionally additive: it adds a new consumer module and focused regressions without editing `promotion.py`, calibration logic, multisource logic, scenario-source selection, resync guards, champion pointers, or workflow files. It can therefore remain isolated while the canonical calibration runner is occupied.

The companion gate-receipt lane may eventually be imported directly after both contracts are proven, but this consumer still revalidates security-critical receipt fields so a malformed or hand-constructed object fails closed at the authorization boundary.
