# Evaluation shared class-state contract

HaxLab evaluation classes must not carry mutable state that is shared across
instances and retained for the lifetime of the validator process.

## Required invariant

Class bodies recursively below `src/haxlab/evaluation/` must not initialize
class attributes with mutable list/dict/set literals or comprehensions, nested
tuple containers that contain those values, or common mutable constructors such
as `list`, `dict`, `set`, `bytearray`, `collections.deque`,
`collections.defaultdict`, and `collections.OrderedDict`.

The detector resolves module/class imports, simple assignment aliases, and
constant `getattr(...)` constructor aliases.

Per-instance state remains allowed. In particular, method-local containers and
`dataclasses.field(default_factory=...)` are valid because they do not create one
mutable object shared by every instance.

## Why this is distinct

The mutable-global contract (#167/#168) guards module-level state and `global`
mutation. The mutable-default contract (#261/#262) guards objects retained on
function defaults. A mutable class attribute is stored on the class object
instead, so it survives across calls and instances without violating either
existing contract.

The memoization-state lane #323/#326 covers decorator-created caches and
descriptors, not explicit class containers. These two lanes therefore protect
different process-lifetime state channels.

## Proof

`tests/test_evaluation_class_state_contract.py` parses every evaluation module
and regression-tests plain, annotated, multi-target, aliased, nested-class, and
constructor-based shared state while preserving legitimate per-instance state.

The branch-scoped self-hosted proof runs the focused contract and adjacent
canonical Arena-v2 evaluation regressions. Green evidence is narrow validation
only; it does not authorize canonical PR #19 integration, resync, threshold
changes, or champion promotion.
