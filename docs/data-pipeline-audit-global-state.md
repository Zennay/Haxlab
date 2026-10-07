# Data-pipeline auditor module-global mutation contract

HaxLab data-pipeline auditors are evidence verifiers that can be imported and
called repeatedly by long-lived workers. An audit result must not change merely
because another audit invocation mutated state retained by the module.

## Required invariant

Every Python module recursively named `*_audit.py` below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

must not mutate module-global containers during audit execution and must not use
`global` declarations to rebind process-lifetime module state.

The contract tracks module-scope list/dict/set literals and comprehensions plus
common mutable-container constructors. It rejects runtime mutating methods,
item/attribute mutation rooted in those globals, and explicit `global`
rebinding.

## Preserved declarative boundary

Existing auditors legitimately use module-level sets such as manifest schema
field allowlists. Those objects are created once at import time and treated as
declarative constants. This contract deliberately allows their declaration and
read-only membership use; it rejects attempts to mutate them later.

Immutable schema labels, numeric thresholds, tuples, strings, frozensets and
per-call local scratch containers remain allowed. Local variables that shadow a
module-global name are also local state and are not flagged.

## Separation from other contracts

Ambient determinism guards environment/time/randomness inputs. Process-state
isolation guards mutation of host-process state such as cwd, environment,
signals and interpreter hooks. The import-layer boundary guards dependencies on
higher product/control packages.

This contract covers a different failure mode: call-order dependence caused by
runtime mutation of data retained inside the audit module itself.

It changes no auditor implementation, producer, runtime schema/state,
evaluation logic, model, threshold or champion pointer.

## Staging and proof

`tests/test_data_pipeline_audit_global_state_contract.py` scans all current and
future data-pipeline audit modules with Python ASTs and regression-tests
container mutation, subscript mutation, global rebinding, declarative schema
sets, local scratch state and local-name shadowing.

The focused test and this contract are staged first. Exact-head self-hosted
proof is added only after the active #228 validation lane releases the
serialized HaxLab runner; integration still requires adjacent audit-contract
compatibility on the final live head.
