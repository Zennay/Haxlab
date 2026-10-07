# Evaluation control-plane import boundary

The Arena/promotion layer decides whether evidence is good enough to advance a
model. Those decisions must not silently acquire dependencies on mutable live
state, daemon state, training orchestration, ingestion state, or autonomous
research control loops.

This contract keeps `src/haxlab/evaluation/` as a decision layer over explicit
inputs rather than a backdoor into HaxLab's mutable control plane.

## Forbidden first-party dependencies

Production Python modules recursively below `src/haxlab/evaluation/` must not
import these package families:

- `haxlab.runtime`
- `haxlab.live`
- `haxlab.learning`
- `haxlab.research`
- `haxlab.ingestion`
- `haxlab.training`

Relative imports that escape the `haxlab.evaluation` package are also
forbidden because they can bypass the absolute-prefix checks.

## Allowed boundary

The contract intentionally allows:

- imports within `haxlab.evaluation`;
- read-only analysis helpers already used by the canonical Arena source, such as
  `haxlab.analysis.roles`;
- stable content hashing utilities such as `haxlab.hashing`;
- Python standard-library data parsing and validation helpers.

If a future evaluation feature genuinely needs a new first-party dependency,
that dependency should be reviewed explicitly rather than introduced through a
mutable runtime package.

## Why this is separate

This contract is not a network/subprocess hermeticity rule and is not a
serialization-safety rule. A module can be fully offline yet still couple a
promotion decision to mutable daemon or training state through an ordinary
Python import. That is the dependency class guarded here.

It complements the existing determinism, optimization-safety, hermeticity,
safe-deserialization, evidence-I/O, policy-config, exact-head receipt, and
resync contracts.

## Proof

`tests/test_evaluation_control_plane_import_contract.py` scans every production
Python module in the evaluation tree with the AST and regression-tests direct,
aliased, from-import, and relative-escape forms.

A later branch-scoped self-hosted workflow must validate the exact immutable
candidate SHA. Green proof for this contract is not merge or promotion
authorization for canonical Arena-v2 PR #19.
