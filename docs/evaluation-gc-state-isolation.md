# Evaluation garbage-collector state isolation

Evaluation gates must be repeatable inside a long-lived Python process. They may
inspect garbage-collector state, but they must not change process-lifetime GC
configuration or the callback registry.

## Invariant

Python modules under `src/haxlab/evaluation/` must not call:

- `gc.enable()` / `gc.disable()`;
- `gc.set_debug(...)`;
- `gc.set_threshold(...)`;
- `gc.freeze()` / `gc.unfreeze()`.

They also must not replace, delete, or mutate `gc.callbacks`, whether reached
directly, through an alias, through a bound mutator, via an unbound `list`
mutator, through `operator`, or by constant `getattr(...)` reflection.

Read-only inspection such as `gc.isenabled()`, `gc.get_debug()`,
`gc.get_threshold()`, `gc.get_count()`, `gc.get_stats()`,
`gc.get_objects()`, and immutable snapshots of `gc.callbacks` remains valid.

## Why this is a separate state boundary

The existing evaluation contracts cover mutable Python globals, OS/interpreter
process hooks, memoization, class/function/module-object state, context-local
state, logging/warnings registries, and atexit callbacks. The garbage collector
has its own interpreter-global configuration and callback registry that can
persist across otherwise pure gate calls.

Changing these settings inside an evaluation gate can alter collection timing,
callback execution, memory pressure, and the behavior of later evaluations in
the same worker. That makes evidence call-order dependent.

## Enforcement

`tests/test_evaluation_gc_state_contract.py` recursively parses every
evaluation module and resolves:

- direct and aliased `gc` imports;
- direct callable imports;
- simple, annotated, named, chained, tuple/list and bound-mutator aliases;
- constant `getattr(...)`;
- unbound list/operator mutation;
- direct, subscript, augmented and reflective callback-registry mutation;
- lexical function/argument shadowing.

Wildcard `from gc import *` is rejected because mutation provenance becomes
ambiguous.

## Acceptance

The dedicated self-hosted proof must establish on the exact live branch head:

1. exact checkout and branch-head identity;
2. Python compilation;
3. focused GC-state contract tests;
4. adjacent canonical Arena-v2 regressions;
5. marker `HAXLAB_EVALUATION_GC_STATE_RESULT=green`.

This lane is additive validation only and does not change product logic,
thresholds, policies, evaluation evidence, models, champion/promotion state, or
the canonical Arena-v2 workflow.
