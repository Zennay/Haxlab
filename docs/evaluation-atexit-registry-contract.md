# Evaluation atexit-registry contract

Evaluation code must not register, unregister, clear, or execute process-exit callbacks through Python's `atexit` registry.

The registry persists for the lifetime of the host process. Touching it from an evaluation gate therefore creates hidden cross-call state: a later validation run can inherit callbacks installed by an earlier one, and callback execution at interpreter shutdown can produce unrelated side effects long after the gate completed.

The additive contract in `tests/test_evaluation_atexit_registry_contract.py` rejects:

- `atexit.register`;
- `atexit.unregister`;
- the private registry mutation/execution helpers `atexit._clear` and `atexit._run_exitfuncs`;
- direct imports, module aliases, assignment aliases, annotated aliases and constant-`getattr(...)` spellings of those calls.

This scope is separate from signal/interpreter process-state mutation (#200/#201), hidden threads/tasks (#211/#212), mutable module globals (#167/#168), memoization (#323/#326), function-attribute state (#342/#344), context-local state (#348/#351), and logging/warnings registries (#355/#357).

No evaluation implementation, thresholds, evidence schema, policy, model, champion pointer, promotion state, or canonical Arena-v2 workflow changes are made.

Acceptance requires an exact-head self-hosted proof and the adjacent canonical Arena-v2 evaluation regression suite.
