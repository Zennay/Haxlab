# Evaluation module-global state contract

HaxLab evaluation decisions should be reproducible from explicit inputs. A gate
must not produce a different result merely because another evaluation call ran
earlier in the same long-lived Python process.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not keep
mutable process-lifetime state at module scope.

The contract rejects module-level:

- list, dict, and set literals;
- list/dict/set comprehensions;
- common mutable-container constructors such as `list`, `dict`, `set`,
  `bytearray`, `collections.defaultdict`, `collections.deque`, and
  `collections.OrderedDict`, including import aliases;
- tuples that directly contain one of those mutable containers;
- `global` declarations that permit functions or classes to mutate
  module-level state at runtime.

Immutable schema labels, numeric thresholds, tuples, strings, and frozensets are
allowed. Local lists/dicts/sets inside a function are also allowed because they
are recreated per call and are not process-lifetime gate state.

## Why this is distinct from ambient determinism

The ambient determinism contract guards random sources, environment reads,
wall-clock time, UUID/secrets entropy, and similar external nondeterminism.
This contract guards a different failure mode: **call-order dependence** caused
by state retained inside the evaluation module itself.

Keeping both boundaries means an exact evidence payload can be evaluated
repeatedly in one worker process without a hidden cache/counter/list carrying
information from a previous run.

## Proof

`tests/test_evaluation_global_state_contract.py` recursively scans the
production evaluation tree using Python ASTs and regression-tests mutable
literals, constructor aliases, nested containers, runtime `global` mutation,
and allowed immutable/local state.

The branch-scoped self-hosted proof verifies the immutable event SHA, compiles
the evaluation package and contract, runs the focused regression, and emits
`HAXLAB_EVALUATION_GLOBAL_STATE_RESULT=green` only after success.

A green result proves this narrow purity contract only. It does not authorize
canonical Arena-v2 PR #19 merge or champion promotion.
