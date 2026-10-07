# Evaluation process-state alias isolation

## Purpose

Canonical Arena-v2 evaluation code must not mutate process-lifetime state through aliases that hide the original mutation target. The earlier process-state contract (#200/#201) covers direct calls, imports and constant-`getattr` spellings. This additive follow-up closes the remaining aliasing boundary without changing evaluation product code.

## Guarded state

The contract follows aliases to:

- working-directory/environment/umask mutation;
- signal and locale mutation;
- interpreter hooks such as tracing/profiling/recursion settings;
- `os.environ`, `sys.path`, and `sys.modules` mutation.

It resolves direct imports, module aliases, assignment/annotated/named aliases, bound mutator aliases and constant `getattr(...)`. It also rejects common unbound `dict`/`list` mutators and `operator.setitem/delitem` when their target resolves to a guarded process-state object.

Read-only aliases such as `env = os.environ; env.get(...)` remain valid. Function parameters and later local assignments shadow outer aliases so ordinary local containers are not misclassified as process state.

## Scope

This lane is additive validation only. It does not modify #201's owned files, evaluation implementation, thresholds, policies, models, evidence, champion/promotion state, canonical workflows, or current-main resync state.

Acceptance requires an exact/live-head self-hosted proof of the focused contract plus adjacent canonical Arena-v2 evaluation regressions.
