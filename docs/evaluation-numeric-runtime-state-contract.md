# Evaluation numeric runtime-state contract

HaxLab evaluation code must not change persistent numeric runtime configuration
that can make later gate decisions depend on call order or host process history.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not mutate:

- the active `decimal` context through `setcontext()`, active-context
  attribute assignment, trap/flag mutation, or context-clearing mutators;
- NumPy process/thread numeric configuration through `seterr`,
  `seterrcall`, `set_printoptions`, or `setbufsize`;
- Torch default numeric/runtime configuration through default dtype/device,
  grad-mode, thread-count, deterministic-algorithm, or deterministic-debug setters;
- known Torch backend numeric flags such as cuDNN determinism/benchmark and
  CUDA matmul TF32/reduced-precision controls.

The contract resolves normal import aliases, direct-import aliases, simple
assignment aliases, and constant-`getattr(...)` spellings. Read-only inspection,
ordinary tensor/array creation, explicit local `decimal.Context` objects, and
scoped `decimal.localcontext()` mutation remain allowed.

## Why this is distinct

The ambient-determinism lane (#147/#148) rejects entropy, wall clock, environment
reads, and module-global random APIs. The process-state lane (#200/#201 and
follow-up #369/#371) guards OS/interpreter state such as cwd, environment,
imports, signals, locale, tracing and recursion controls. Logging/warnings
registries are separately owned by #353/#356 and #355/#357.

Numeric library runtime configuration is a different persistent state surface:
a valid-looking evaluation call can change precision, error handling, backend
algorithm behavior or default dtypes for a later call without changing the
later call's explicit inputs.

## Proof

`tests/test_evaluation_numeric_runtime_state_contract.py` parses the complete
evaluation package and regression-tests direct calls, aliases, reflective access,
active Decimal context mutation and Torch backend assignments.

The branch-scoped self-hosted workflow compiles the package and contract, runs
the focused tests, then runs adjacent canonical Arena-v2 evaluation regressions
before emitting `HAXLAB_EVALUATION_NUMERIC_RUNTIME_STATE_RESULT=green`.

A green result proves only this narrow state-isolation invariant. It does not
authorize canonical Arena-v2 PR #19 integration/resync or champion promotion.
