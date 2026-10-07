# Evaluation process-wide instrumentation isolation

Evaluation gates run inside long-lived Python validator and self-hosted runner
processes. They may inspect runtime instrumentation state, but they must not
install or reconfigure process-wide instrumentation that survives the current
evaluation call.

## Invariant

Python modules under `src/haxlab/evaluation/` must not mutate:

- Python audit hooks through `sys.addaudithook(...)`;
- faulthandler process handlers through `enable`, `disable`, `register`,
  `unregister`, delayed-dump scheduling or cancellation;
- tracemalloc tracing state through `start`, `stop`, or `reset_peak`;
- threading-wide trace/profile hooks;
- Python 3.12+ `sys.monitoring` tool ids, callbacks, event masks or restart
  state.

Read-only inspection remains valid, including `faulthandler.is_enabled()`,
`tracemalloc.is_tracing()`, `tracemalloc.get_traced_memory()`,
`threading.gettrace()`, and `threading.getprofile()`.

## Why this is separate

The existing process-state contract (#200/#201) covers cwd, environment,
import state, signals, umask, locale and selected `sys` interpreter setters,
including direct `sys.settrace` / `sys.setprofile`. Other focused lanes own
logging/warnings registries, atexit state, GC state, host resource scheduling
and the builtins namespace.

The instrumentation APIs covered here are separate process-lifetime registries
or facilities. In particular, an audit hook cannot be removed once installed.
Changing them inside an evaluation helper can therefore alter later gates,
diagnostics and failure behavior even when the evaluation's explicit evidence
inputs are identical.

## Enforcement

`tests/test_evaluation_runtime_instrumentation_contract.py` recursively parses
all evaluation modules and resolves:

- normal module aliases and direct callable imports;
- assignment, chained and tuple/list aliases;
- constant-string `getattr(...)` access;
- lexical function/argument shadowing.

Wildcard imports from instrumentation-bearing modules are rejected because
mutation provenance becomes ambiguous.

## Acceptance

The branch-scoped proof must establish on the exact requested SHA:

1. exact immutable checkout and live branch-head identity;
2. Python compilation;
3. focused runtime-instrumentation contract tests;
4. adjacent canonical Arena-v2 regression tests;
5. marker `HAXLAB_EVALUATION_RUNTIME_INSTRUMENTATION_RESULT=green`.

This lane is additive validation only. It does not change evaluation product
logic, policy thresholds, evidence formats, models, champion/promotion state,
calibration inputs or canonical Arena-v2 workflows.
