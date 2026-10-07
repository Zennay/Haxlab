# Evaluation runtime import-cycle contract

The evaluation package is a gate library as well as a set of CLI entry points.
Its internal runtime dependency graph must stay acyclic so importing one gate
cannot expose another module in a partially initialized state.

## Required invariant

Every runtime import edge between Python modules recursively below
`src/haxlab/evaluation/` is collected into one deterministic directed graph.
That graph must contain no direct or transitive cycle.

The contract understands:

- absolute imports such as `from haxlab.evaluation.models import ...`;
- package imports that name an internal child module;
- relative imports such as `from .duel_gate import ...`;
- nested evaluation packages that may be added later.

Imports guarded solely by `typing.TYPE_CHECKING` (including a normal alias and
`if not TYPE_CHECKING` runtime branches) are excluded because they do not
execute as runtime dependencies.

## Boundary

This is intentionally separate from existing import validation:

- #159/#161 controls the package boundary and forbids mutable first-party
  control-plane imports;
- #160/#162 proves evaluation modules import without side effects.

Neither contract detects a circular dependency entirely inside
`haxlab.evaluation`. This lane changes no product implementation, evaluation
threshold, model, champion pointer, source suite or canonical Arena-v2 workflow.

At the canonical base, the runtime graph is already acyclic and contains the
notable edges `multisource_duel -> duel_gate` and `promotion -> models`.

## Proof

`tests/test_evaluation_import_cycle_contract.py` recursively builds the graph
from Python ASTs and applies a deterministic depth-first cycle detector.
Self-tests cover self-cycles, transitive cycles, absolute/relative imports and
TYPE_CHECKING-only exclusions.

The branch-scoped self-hosted workflow verifies the exact event SHA, compiles
the package and contract, runs the focused graph regressions, then runs the 12
adjacent canonical Arena-v2 evaluation regression files used by the existing
verification bundle (excluding owner-reserved blocker #92).

A green result is narrow validation evidence only. It does not authorize
canonical PR #19 merge or champion promotion.
