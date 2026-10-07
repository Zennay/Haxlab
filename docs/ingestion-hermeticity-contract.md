# Ingestion hermeticity contract

HaxLab's V0 data foundation consumes already-exported Discord JSON and HBR2
files. The ingestion package must therefore remain an offline, local
transformation layer: raw evidence in, deterministic derived evidence out.

## Protected boundary

`tests/test_ingestion_hermeticity_contract.py` recursively scans every Python
module below `src/haxlab/ingestion/`. The static contract rejects
network-capable imports, subprocess/process-launch surfaces and dynamic import
entrypoints that could make ingestion depend on ambient external state.

The checker resolves normal import aliases, assignment aliases and constant
`getattr(...)` aliases for guarded calls. Representative self-tests lock those
resolution paths. It also rejects multiprocessing/process-creation surfaces so
work cannot escape the calling ingestion process.

A second proof imports every ingestion module in a fresh isolated Python
process with bytecode writes disabled and an audit hook installed. Import-time
filesystem mutation, subprocess/socket activity, stdout/stderr and working
directory artifacts fail closed.

Local filesystem behavior during explicitly invoked ingestion operations,
hashing, JSON serialization, path handling and other deterministic primitives
remain allowed. This contract does not change parser, matcher, discovery,
archive, runtime, learning, evaluation, model, champion or threshold behavior.

## Why this is separate from auditor hermeticity

The existing data-pipeline audit hermeticity contract covers only recursively
named `*_audit.py` verifier modules under ingestion, learning and runtime.
Those verifiers must be hermetic because they validate already-published
evidence.

This contract covers the ordinary ingestion producer/parser/matcher package
itself. It prevents a future implementation from silently fetching remote
state, spawning helper processes or dynamically loading behavior while
constructing M0 evidence.

## Proof

The branch proof workflow checks the exact live branch head on the self-hosted
HaxLab runner, compiles source/tests, runs this focused contract and adjacent
ingestion regressions. Normal HaxLab CI remains the integration gate.
