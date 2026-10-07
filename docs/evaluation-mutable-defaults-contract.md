# Evaluation mutable-default argument contract

HaxLab evaluation decisions must not depend on mutations retained by a previous
call in the same Python process.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not use
mutable objects as function, method, async-function, or lambda default values.

The contract rejects positional and keyword-only defaults created from:

- list, dict, and set literals;
- list/dict/set comprehensions;
- nested tuple defaults containing one of those mutable containers;
- common mutable constructors such as `list`, `dict`, `dict.fromkeys`,
  `set`, `bytearray`, `collections.defaultdict`,
  `collections.deque`, and `collections.OrderedDict`, including import and
  module-level assignment aliases.

Immutable constants, tuples, `None`, frozen-set defaults, function objects,
and intentionally immutable constructed defaults such as HaxLab's frozen policy
dataclasses remain allowed.

## Why this is distinct

The module-global-state contract (#168) rejects mutable state assigned at module
scope and runtime `global` mutation. A mutable default has a different lifetime:
Python evaluates it once when the function object is created and then reuses the
same object on every omitted-argument call. It can therefore create hidden
call-order dependence without any mutable module assignment.

This contract is also separate from ambient nondeterminism (#148), host-process
state mutation (#201), foreground liveness (#222), destructive filesystem
mutation (#248), and runtime output purity (#252).

## Proof

`tests/test_evaluation_mutable_defaults_contract.py` recursively parses every
evaluation module and checks function, async-function, method, and lambda
defaults. Focused regressions cover positional defaults, keyword-only defaults,
import aliases, assignment aliases, nested containers, async functions, and lambdas.

The branch-scoped self-hosted proof additionally compiles the evaluation tree,
runs the focused contract, and executes the adjacent canonical Arena-v2
evaluation regression suite before emitting
`HAXLAB_EVALUATION_MUTABLE_DEFAULTS_RESULT=green`.

A green result proves only this narrow purity invariant. It does not authorize
canonical Arena-v2 PR #19 integration, resync, or champion promotion.
