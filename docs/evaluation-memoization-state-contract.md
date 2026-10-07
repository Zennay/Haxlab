# Evaluation memoization-state contract

HaxLab evaluation gates must derive each decision from the evidence and policy
supplied to that call. They must not retain hidden memoized results that can make
a later invocation depend on earlier process history.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not use:

- `functools.cache`;
- `functools.lru_cache`;
- `functools.cached_property`.

The contract rejects those helpers when used as decorators or ordinary wrappers.
It resolves direct imports, module aliases, simple assignment aliases, annotated
or named assignment aliases, constant `getattr(...)` (including default-value)
spellings, `functools.__dict__`/`vars(functools)` lookups, and rejects wildcard
`functools` imports that would expose memoizers without an auditable binding.

Stateless helpers such as `functools.partial`, `functools.reduce`, and
`functools.wraps` remain allowed. Per-call local dictionaries also remain allowed:
their lifetime is explicit and bounded by the invocation rather than the Python
process.

## Why this is distinct

The mutable-global contract (#167/#168) rejects explicit mutable module-level
containers and `global` mutation. The mutable-default contract (#261/#262)
rejects state retained on function default objects. Memoization wrappers retain
state in a decorator-created wrapper or descriptor instead, so neither contract
covers this call-history channel.

The ambient-determinism contract (#147/#148) rejects randomness, wall-clock and
similar ambient inputs, but a perfectly deterministic function can still return
stale evidence when its result is cached across calls. The immutable-evidence
contract (#218/#220) protects evidence objects from mutation; it does not prevent
reusing an old decision.

## Proof

`tests/test_evaluation_memoization_state_contract.py` recursively parses the
complete evaluation package and regression-tests direct, imported, aliased,
factory-call, reflective mapping lookup, wildcard-import, and constant-`getattr`
memoization spellings.

The branch-scoped self-hosted workflow compiles the evaluation package and
contract, runs the focused detector, then runs adjacent canonical Arena-v2
evaluation regressions. A green result proves only this narrow invariant; it does
not authorize canonical PR #19 integration, resync, threshold changes, or champion
promotion.
