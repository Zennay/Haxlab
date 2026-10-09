# Data-pipeline auditor memoization-state contract

HaxLab data-pipeline auditors verify evidence that can legitimately change between invocations. A long-lived worker must not reuse a verdict or filesystem-derived value merely because a later call has the same Python arguments.

## Required invariant

Every Python module recursively named `*_audit.py` below:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`;

must remain free of stdlib function-object memoization through:

- `functools.cache`;
- `functools.lru_cache`;
- `functools.cached_property`.

The contract rejects direct decorators, decorator factories and runtime wrappers. Import aliases, module aliases, assignment aliases, direct tuple/list alias unpacking, and constant `getattr(...)` spellings are resolved so the stateful capability cannot be hidden behind a local name. Wildcard imports from `functools` are rejected because they can introduce the memoizers without an explicit binding the scanner can safely resolve. Dynamic `getattr(functools, name)` access is also rejected fail-closed because a non-constant attribute name could select a stateful memoizer at runtime.

## Why this is distinct

The module-global-state contract guards mutation of containers retained at module scope. The mutable-default contract guards objects retained in function default arguments. Neither covers state stored inside wrapper objects created by `cache` or `lru_cache`, nor values retained by `cached_property`.

That distinction matters for evidence auditors. A cached call such as `audit(path)` could return a result derived from an earlier version of the file at that same path after the evidence was replaced or updated. Exact-head code identity does not make mutable external evidence immutable.

## Preserved operations

Stateless helpers such as `functools.partial` and `functools.wraps` remain allowed. Ordinary local variables, parameters named `cache`, declarative labels, immutable constants and deterministic per-call computation are also unaffected.

## Scope

This is additive validation only. It changes no auditor implementation, producer, runtime database/schema, ingestion or learning artifact, evaluation policy, model, threshold or champion state.

## Proof

`tests/test_data_pipeline_audit_memoization_state_contract.py` scans every current and future data-pipeline `*_audit.py` module and regression-tests decorators, wrapper factories, import/assignment aliases, constant `getattr(...)`, tuple/list alias unpacking and lexical parameter shadowing.

The branch-scoped self-hosted proof runs on every push to the validation branch, binds checkout to the exact candidate SHA, compiles the focused contract, executes it together with adjacent global-state, mutable-default and determinism contracts, and confirms repository-wide test collection. A green proof is evidence only for this narrow no-memoization invariant.
