# Evaluation exception-boundary contract

HaxLab evaluation code decides whether evidence may influence model promotion.
Unexpected implementation or runtime defects must therefore remain visible and
must not be silently converted into apparently valid gate results by overly
broad exception handling.

## Required invariant

Every production Python module recursively below `src/haxlab/evaluation/`
must use explicit expected exception classes.

The contract rejects:

- bare `except:`;
- `except Exception`;
- `except BaseException`;
- tuples containing either broad base class;
- qualified or imported aliases of the broad built-in exception classes;
- `contextlib.suppress(Exception)` or
  `contextlib.suppress(BaseException)`, including aliases.

Explicit expected failures remain allowed, for example
`OSError`, `ValueError`, `UnicodeError`, `JSONDecodeError`, or a tuple of
such concrete classes.

## Why this is fail-closed

Broad handlers can accidentally catch programmer defects, assertion-like
invariant failures, cancellation/system-exit behavior, or future exceptions
that were never part of the evidence contract. If such an exception is mapped
to a normal fallback value, the gate can continue using incomplete evidence.

The invariant does not require every expected input error to crash. It requires
the code to name the failure classes it intentionally handles so unexpected
behavior cannot disappear silently.

## Boundary

This is a static exception-boundary contract only. It does not change existing
Arena logic, thresholds, calibration inputs, source suites, models, champion
state, or error-return semantics. It complements the independent determinism,
optimization-safety, hermeticity, safe-deserialization, control-plane import,
evidence-I/O, and exact-head gate contracts.

## Proof

`tests/test_evaluation_exception_boundary_contract.py` recursively scans the
evaluation production tree using Python ASTs and regression-tests aliases,
tuples, broad suppression, and allowed explicit handlers.

The branch-scoped self-hosted proof verifies the immutable event SHA, compiles
the evaluation package and contract, runs the focused regression, and emits
`HAXLAB_EVALUATION_EXCEPTION_BOUNDARY_RESULT=green` only on success.

A green result proves this narrow contract only. It is not merge or champion
promotion authorization for canonical Arena-v2 PR #19.
