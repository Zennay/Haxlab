# Data-pipeline auditor layer boundary

HaxLab's data-pipeline auditors judge already-published evidence. Their verdicts
must stay independent from higher mutable/product-control layers so dependency
direction remains evidence-first: lower data layers are validated before
evaluation, live control, rollout or autonomous training/research consumes them.

## Required boundary

Every Python module recursively named `*_audit.py` below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

must not import these higher-layer package families:

- `haxlab.coaching`
- `haxlab.evaluation`
- `haxlab.live`
- `haxlab.rollout`
- `haxlab.research`
- `haxlab.training`

The contract covers direct `import` statements, `from ... import ...` forms,
`from haxlab import <layer>`, aliases, and equivalent relative imports such as
`from .. import evaluation`.

## Allowed dependencies

This is deliberately not a blanket first-party import ban. Auditors may keep
using the local evidence surfaces they actually validate and stable read-only
helpers, including examples such as:

- `haxlab.runtime.state` from runtime auditors;
- `haxlab.learning.shard_audit` from learning bundle auditors;
- `haxlab.hashing`;
- read-only analysis/schema helpers where the audited data contract requires
  them;
- Python standard-library parsing and validation utilities.

If a future auditor genuinely needs a dependency on coaching/evaluation/live/rollout,
research or training orchestration, that architectural reversal should be
reviewed explicitly rather than entering an integrity gate incidentally.

## Separation from existing contracts

This contract is dependency-direction validation only.

It does not replace:

- import-time purity and network/subprocess hermeticity (#180/#182);
- read-only execution (#185/#187);
- ambient determinism (#183/#184);
- broad-exception safety (#186/#188);
- safe deserialization (#189/#190);
- optimization safety (#191/#192);
- dynamic-loading safety (#193/#195);
- process-state isolation (#198/#197);
- foreground liveness (#223/#224);
- hidden background execution (#225/#227).

No auditor implementation, producer, runtime schema/state, evaluation logic,
model, threshold or champion pointer is modified by this lane.

## Proof

`tests/test_data_pipeline_audit_layer_boundary.py` scans every current and
future data-pipeline `*_audit.py` module with Python's AST and resolves
absolute and relative import targets against the audited module.

The branch-scoped self-hosted workflow binds proof to the exact live branch SHA,
compiles the repository, runs the focused boundary contract and reruns the
adjacent integrated data-pipeline audit contracts.

A green result is data-pipeline integrity evidence only; it does not authorize
Arena-v2 integration, model promotion or live champion changes.
