# Evaluation shared class-state contract

HaxLab evaluation classes must not carry mutable state that is shared across
instances and retained for the lifetime of the validator process.

## Required invariant

Class bodies recursively below `src/haxlab/evaluation/` must not initialize
class attributes with mutable list/dict/set literals or comprehensions, nested
tuple containers that contain those values, or common mutable constructors such
as `list`, `dict`, `set`, `bytearray`, `collections.deque`,
`collections.defaultdict`, `collections.OrderedDict`, `collections.Counter`,
`collections.ChainMap`, `collections.UserDict`/`UserList`, `array.array`, in-memory
`io` buffers, queue containers, `types.SimpleNamespace`, and weak-reference
mapping containers.

The detector resolves module/class imports, simple assignment aliases, constant
`getattr(...)` constructor aliases (including default-value lookups), and
`module.__dict__` / `vars(module)` mapping lookups. Wildcard imports from modules
that expose the banned mutable constructors are rejected because they erase the
binding information required for deterministic static resolution. The
standard-library constructor set is intentionally explicit so immutable wrappers such as `MappingProxyType`
remain valid while common process-lifetime mutable containers fail closed.

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
constructor-based and reflectively-resolved shared state while preserving
legitimate per-instance state.

The branch-scoped self-hosted proof runs the focused contract and adjacent
canonical Arena-v2 evaluation regressions. Green evidence is narrow validation
only; it does not authorize canonical PR #19 integration, resync, threshold
changes, or champion promotion.
