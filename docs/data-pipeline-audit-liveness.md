# Data-pipeline auditor foreground-liveness contract

HaxLab data-pipeline auditors are integrity gates. They must finish under the foreground validation job that owns their evidence and must not park that job waiting for a person, a timer, or a recognized blocking synchronization primitive.

## Required invariant

Python modules recursively named `*_audit.py` below `src/haxlab/ingestion/`, `src/haxlab/learning/` and `src/haxlab/runtime/` must not introduce:

- interactive console prompting via `input()`;
- explicit sleeps via `time.sleep` or `asyncio.sleep`;
- indefinite process parking via `signal.pause`;
- recognized synchronization waits/acquires from `threading` or `multiprocessing`;
- blocking queue receives/joins from `queue` or `multiprocessing`;
- blocking `concurrent.futures.Future.result()` / `.exception()` waits.

Import aliases, assignment aliases and constant-`getattr(...)` spellings are covered. Ordinary dictionary `.get()`, pure local computation and unrelated custom methods merely named `wait` remain allowed.

## Boundary

This contract is additive-only. It changes no auditor implementation and does not touch producers, runtime state/schema, scanner, archive, analyzer, learning outputs, evaluation code, models or champion state.

It is separate from the existing data-pipeline contracts for read-only behavior, network/process hermeticity, safe deserialization, broad-exception handling and ambient determinism, and from the separately owned optimization, dynamic-loading and process-state lanes.

## Proof

`tests/test_data_pipeline_audit_liveness_contract.py` recursively scans the current data-pipeline auditor modules with Python ASTs. Its self-tests cover direct calls, import aliases, assignment aliases, constant `getattr`, synchronization objects, futures and blocking queues, while freezing allowed dictionary access and unrelated custom methods.

The branch-scoped proof workflow validates the exact live branch SHA, compiles the audit surfaces, runs the focused liveness contract and reruns the adjacent audit contract suite.

A green result is integrity evidence only; it does not authorize unrelated evaluation or champion changes.
