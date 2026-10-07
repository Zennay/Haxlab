# Data-pipeline auditor runtime import-cycle contract

HaxLab data-pipeline auditors are integrity libraries as well as CLI entry
points. Their runtime dependency graph must remain acyclic so importing one
auditor cannot expose another in a partially initialized state.

## Required invariant

Every Python module recursively named `*_audit.py` below `src/haxlab/` is a
node in one deterministic directed graph. This deliberately includes audit
helpers reached outside the three primary ingestion/learning/runtime roots;
today that adds `haxlab.skill.leaderboard_audit`, which is imported by
`manifest_source_audit`. Runtime imports from one auditor
to another are edges. The graph must contain no direct or transitive cycle.

The contract understands absolute imports, package-child imports, relative
imports and future nested audit packages. Self-imports remain real edges and
therefore fail as direct cycles.

Imports guarded solely by `typing.TYPE_CHECKING` are excluded because they do
not execute at runtime. Normal aliases and `if not TYPE_CHECKING` runtime
branches are handled.

## Current graph

The current graph is intentionally sparse. Known legitimate one-way
dependencies include:

- `manifest_source_audit -> manifest_audit`;
- `manifest_source_audit -> skill.leaderboard_audit`;
- `shard_bundle_audit -> shard_audit`.

The contract protects those directions from later becoming cycles.

## Separation from existing import contracts

The dependency-direction contract (#226/#228) prevents data-pipeline auditors
from importing higher product/control packages such as evaluation, live,
rollout, research and training. Import-time hermeticity guards side effects.

Neither rule detects a cycle formed entirely among otherwise allowed audit
modules, including a cycle that leaves a primary data-pipeline package through
an imported audit helper and returns later. This contract owns only that intra-auditor graph property and changes
no auditor implementation, producer, runtime schema/state, model or champion
pointer.

## Proof

`tests/test_data_pipeline_audit_import_cycle_contract.py` builds the runtime
auditor graph from Python ASTs and applies a deterministic depth-first cycle
detector. Self-tests cover direct/transitive cycles, absolute/relative/package
child imports and TYPE_CHECKING-only exclusions.

The branch-scoped self-hosted proof binds validation to the exact live branch
SHA, compiles the repository, runs the focused graph contract and reruns the
integrated data-pipeline audit safety bundle before emitting a green marker.
