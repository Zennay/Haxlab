# Evaluation module-object state contract

HaxLab evaluation gates must not retain call-history by mutating ordinary
attributes or nested containers rooted at module-level singleton objects.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not:

- assign, annotate-assign, augmented-assign, or delete attributes rooted at a
  module-level assigned object;
- mutate subscripts below such persistent objects;
- call common mutating list/dict/set methods on persistent object state;
- hide the same mutation behind simple state aliases, bound-mutator aliases, or
  constant `getattr(object, "attr")`;
- mutate persistent nested containers through unbound built-in mutators or
  `operator.setitem` / `operator.delitem`.

Read-only access to module-level objects is allowed. Per-call locals, function
parameters that shadow a module name, ordinary instance state, function-object
attributes, and class-body shared state are outside this detector's ownership.

## Why this is distinct

The mutable-global contract (#167/#168) rejects module-level mutable container
values and runtime `global` mutation. An arbitrary singleton such as
`STATE = Holder()` is not itself one of those container values, while its
ordinary attributes can still hold mutable process-lifetime state.

The immutable-evidence bypass contract (#218/#220) rejects reflective mutation
through `setattr`, dunder setters, `__dict__`, and `vars(...)`; ordinary
syntax such as `STATE.cache = {}` or `STATE.cache.update(...)` does not use
those escape hatches.

Shared class state (#329/#335), memoization (#323/#326), and function-object
attributes (#342/#344) remain independently owned invariants.

## Proof

`tests/test_evaluation_module_object_state_contract.py` recursively parses the
complete evaluation package. The focused regressions cover direct, annotated,
augmented, deletion, subscript, alias, bound-mutator, constant-`getattr`,
unbound built-in, and operator mutation while preserving local/instance/read-only
behavior and the neighboring owners' scopes.

The branch-scoped self-hosted proof compiles the evaluation package and contract,
runs the focused contract, then runs adjacent canonical Arena-v2 evaluation
regressions before emitting
`HAXLAB_EVALUATION_MODULE_OBJECT_STATE_RESULT=green`.

A green result proves only this narrow state-isolation invariant. It does not
authorize canonical Arena-v2 PR #19 integration/resync or champion promotion.
