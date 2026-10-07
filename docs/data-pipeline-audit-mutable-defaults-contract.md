# Data-pipeline auditor mutable-default argument contract

HaxLab data-pipeline auditors must not retain hidden call-order state through
Python function default objects.

## Required invariant

Every Python module recursively named `*_audit.py` below `src/haxlab/` must
avoid mutable objects as function, method, async-function, or lambda default
values.

The contract rejects positional and keyword-only defaults created from:

- list, dict, and set literals;
- list/dict/set comprehensions;
- nested tuple defaults containing mutable containers;
- common mutable constructors such as `list`, `dict`, `dict.fromkeys`,
  `set`, `bytearray`, `collections.defaultdict`, `collections.deque`,
  and `collections.OrderedDict`;
- imported aliases, module-level assignment aliases, and constant
  `getattr(...)` spellings of those constructors.

Immutable constants, tuples, `None`, frozen-set defaults, function objects, and
intentionally immutable constructed policy/config objects remain allowed.

## Why this is distinct

The module-global-state contract integrated by #233/#237 guards runtime
mutation or rebinding of module-scope state. Mutable defaults have a different
lifetime: Python evaluates them when the function object is created and reuses
the same object on later calls that omit the argument.

An auditor can therefore become call-order dependent without mutating any
module-global container. That can make repeated integrity checks disagree inside
one long-lived worker even when their explicit inputs are identical.

This contract remains separate from optimization safety (#192), dynamic loading
(#195), host process-state mutation (#197), dependency direction (#226/#228),
foreground/background liveness (#223/#224 and #225/#227), host-process
termination (#229/#230), and the runtime import-cycle lane (#240/#242).

## Proof

`tests/test_data_pipeline_audit_mutable_defaults_contract.py` recursively
parses every current and future `*_audit.py` module under `src/haxlab/`.
Focused regressions cover positional and keyword-only defaults, async functions,
lambdas, import aliases, assignment aliases, constant `getattr(...)`
reflection, nested tuple containers, and representative immutable defaults.

The branch-scoped self-hosted proof binds execution to the exact live branch
head, compiles the repository, runs the focused contract, and reruns the
integrated data-pipeline audit safety bundle before emitting
`HAXLAB_DATA_PIPELINE_AUDIT_MUTABLE_DEFAULTS_RESULT=green`.

A green result proves only this narrow validation invariant. It does not modify
auditor/product implementation, source receipts, runtime state, model/evaluation
logic, or champion state.
