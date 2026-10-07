# Evaluation immutability-bypass contract

HaxLab's Arena-v2 evaluation schema intentionally uses frozen evidence, policy
and decision values. Schema-level `frozen=True` is necessary but not sufficient:
Python exposes reflection primitives that can mutate an instance or class after
construction and therefore bypass the invariant that a validated value stays
unchanged while a gate consumes it.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
use reflective mutation escape hatches:

- generic `setattr(...)` / `delattr(...)`;
- `object.__setattr__` / `object.__delattr__`;
- `type.__setattr__` / `type.__delattr__`;
- assignment/deletion through an object's `__dict__`;
- mutating methods on `__dict__` or `vars(...)` mappings;
- functional mapping mutators such as `dict.__setitem__` or
  `operator.setitem` when they target those reflection mappings.

Aliases, assignment aliases and constant-`getattr(...)` forms are part of the
same forbidden surface.

Read-only reflection remains valid: `getattr`, `hasattr`, `vars(...)` reads
and `__dict__` inspection are allowed. `dataclasses.replace(...)` is allowed
because it creates a new value rather than mutating the validated object.

## Boundary

This is deliberately separate from current validation owners:

- #109/#110 freezes schema field order/surface and dataclass immutability;
- #167/#168 prevents mutable module-global evaluation state;
- #200/#201 prevents process-global state mutation;
- #218 only proves that production evaluation code does not bypass object-level
  immutability at runtime.

No evaluation implementation, schema, policy threshold, model, champion pointer
or canonical Arena-v2 workflow is modified.

## Proof

`tests/test_evaluation_immutability_bypass_contract.py` recursively scans the
evaluation package using Python ASTs. Its self-tests cover direct mutation APIs,
builtin aliases, assignment aliases, constant-`getattr(...)`, `__dict__` and
`vars(...)` mutation, functional dict/operator bypasses, and the permitted
read-only/ref replacement surface.

The branch-scoped self-hosted workflow is exact-head and stale-push safe: its
concurrency group is keyed by `github.sha`, and before installing dependencies
it proves the checked SHA is still the live remote branch head. It then compiles
the package, runs the focused contract, and runs the 12 adjacent canonical
Arena-v2 evaluation regression files used by the established compatibility
bundle (excluding owner-reserved blocker #92).

Only terminal green evidence on the final live branch head counts. This remains
narrow validation evidence and does not authorize PR #19 merge or champion
promotion.
