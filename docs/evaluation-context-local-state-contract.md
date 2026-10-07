# Evaluation context-local state contract

HaxLab evaluation gates must not retain hidden module-lifetime state through
Python context-local or thread-local storage primitives.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not create
and retain at module scope:

- `contextvars.ContextVar(...)`;
- `contextvars.Context(...)`;
- `contextvars.copy_context()` snapshots;
- `threading.local()`;
- `_thread._local()`.

The same rule applies through direct imports, module aliases, simple assignment
aliases, annotated/named aliases, constant `getattr(...)`, and constructor calls
nested inside an eagerly evaluated module-level value. Subclassing the thread-local
storage primitives is also forbidden.

Per-call temporary construction remains allowed because it does not retain state
between independent evaluation calls by itself. Ordinary synchronization
primitives such as `threading.Lock()` remain governed by the existing liveness
and background-execution contracts rather than this state contract.

## Why this is distinct

The mutable-global contract (#167/#168) recognizes ordinary mutable containers,
but a module-level `ContextVar` or `threading.local` binding is an object
reference whose mutable values live behind runtime context/thread indirection.

The process-state contract (#200/#201) protects interpreter/OS state such as
environment, cwd, signals, tracing and import tables. The background-execution
contract (#211/#212) prevents spawning workers/tasks. Neither rejects context- or
thread-local storage creation.

Mutable defaults (#261/#262), class shared state (#329), memoization (#323/#326)
and function attributes (#342) protect other process-lifetime storage mechanisms.

## Proof

`tests/test_evaluation_context_local_state_contract.py` recursively parses the
complete evaluation package. Focused regressions cover direct constructors,
imports/aliases, annotated aliases, constant reflection, nested eager values and
thread-local subclassing while preserving local per-call use and lazy factories.

The branch-scoped self-hosted proof compiles the evaluation package and contract,
runs the focused test, then runs adjacent canonical Arena-v2 regressions. Only
after all steps succeed it posts an exact-head receipt on issue #348 and emits
`HAXLAB_EVALUATION_CONTEXT_LOCAL_STATE_RESULT=green`.

A green result proves only this narrow invariant. It does not authorize canonical
Arena-v2 PR #19 integration/resync or champion promotion.
