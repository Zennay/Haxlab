# Closed-Loop Evaluation Arena v2

Arena v2 is HaxLab's mandatory closed-loop evaluation layer. It exists because
offline state→action accuracy does not prove that a policy remains competent
after its own actions create the next states.

## Frozen input

Arena v2 accepts only a frozen promotion-suite manifest with at least three
disjoint replay sources. It verifies every declared source, stadium and
scenario SHA-256 before simulation.

The suite is selected **before** the next challenger is defined or trained.

## Identical-start evaluation

For every source scenario and both team sides the arena executes two episodes:

1. **raw** — the policy controls GK/DM/AM/ST without recovery or live guard;
2. **guarded** — the exact same model and start state with the live-style
   boundary / role-leash / far-stall / OOD recovery guard enabled.

The opponent is the deterministic scripted baseline. Scenario class rotates
between balanced, compact and press using the frozen seed.

Default episode length is 90 seconds, materially longer than the older
30-second replay-duel diagnostic.

## Captured metrics

Per episode, role and aggregate:

- result, goals, territory and progression;
- average/minimum ball distance and near-ball involvement;
- raw and executed action-change rates;
- maximum held-action duration;
- long-stall rate and maximum stall streak;
- mean/max OOD z-score and OOD-spike rate;
- role-target distance and role-deviation rate;
- boundary exposure and boundary-event count;
- formation-order rate;
- team X-span and pairwise-spacing mean + temporal standard deviation;
- runtime errors;
- guard intervention count/rate and intervention reason.

Safety interventions are evidence, not model skill.

## Provenance and immutability

Artifacts are written below:

```text
<output-root>/
  <runtime-model-sha256>/
    <frozen-suite-manifest-sha256>/
      <arena-config-sha256>/
        arena-v2.json
```

Each artifact records the exact:

- arena code ref;
- runtime model SHA-256;
- runtime model schema;
- frozen suite manifest SHA-256;
- source replay SHA-256 values;
- source metadata SHA-256 values;
- stadium SHA-256 values;
- scenario SHA-256 values;
- deterministic seed and complete arena config.

An existing artifact path cannot be overwritten with different bytes.

## Comparison

`haxlab-compare-closed-loop-arena` compares two artifacts only when code ref,
frozen suite, config and source provenance are identical. It fails closed on
coverage or provenance mismatch.

Arena v2 deliberately does **not** define promotion thresholds. The separate
Promotion Gate v2 task must freeze thresholds for rollout stability and
guard-independence before a challenger can use Arena v2 for promotion.
