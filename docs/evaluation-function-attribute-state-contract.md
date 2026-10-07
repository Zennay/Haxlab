# Evaluation function-attribute state contract

HaxLab evaluation gates must not retain call-history through mutable or changing
attributes attached directly to module-level function objects.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not:

- assign, annotate-assign, augmented-assign, or delete an attribute on a
  module-level function object;
- mutate a subscript below such an attribute;
- call common mutating list/dict/set methods on function-attached state;
- hide the same mutation behind a simple function alias, a state-container alias,
  a bound-mutator alias, or constant `getattr(function, "attr")`;
- mutate function-attached containers through unbound built-in mutators or
  `operator.setitem` / `operator.delitem`.

Read-only metadata such as `gate.__name__`, per-call local containers, nested
ephemeral functions, and ordinary instance attributes remain allowed.

## Why this is distinct

The mutable-global contract (#167/#168) inspects module-level assignment values;
`gate.cache = {}` is an attribute assignment and therefore is not a mutable
module-global assignment node.

The immutable-evidence bypass contract (#218/#220) rejects reflective
`setattr`/dunder mapping mutation, but ordinary Python attribute syntax such as
`gate.cache = {}` or `gate.cache.update(...)` does not use those bypasses.

Mutable defaults (#261/#262), class-body shared state (#329), and functools
memoization (#323/#326) protect different process-lifetime storage mechanisms.
A module-level function object's attributes survive for the life of the imported
module and can otherwise make gate results depend on earlier calls.

## Proof

`tests/test_evaluation_function_attribute_state_contract.py` recursively parses
the complete evaluation package. Focused regressions cover direct writes,
annotated/augmented assignment, deletion, subscript mutation, function aliases,
state aliases, bound mutator aliases, constant `getattr`, unbound mutators, and
operator mutation while preserving local/instance/read-only behavior.

The branch-scoped self-hosted proof compiles the evaluation package and contract,
runs the focused test, then runs adjacent canonical Arena-v2 evaluation
regressions before emitting
`HAXLAB_EVALUATION_FUNCTION_ATTRIBUTE_STATE_RESULT=green`.

A green result proves only this narrow invariant. It does not authorize canonical
Arena-v2 PR #19 integration/resync or champion promotion.


## Scope-awareness hardening

The detector distinguishes persistent module-level function bindings from parameters or local variables that shadow the same name. It also resolves module-level function aliases, state-container aliases, bound mutator aliases (including constant `getattr(...)`), builtins mutator aliases, and explicit `global` access. This avoids both false positives on ephemeral locals and false negatives through simple indirection.
