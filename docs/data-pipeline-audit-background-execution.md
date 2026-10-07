# Data-pipeline auditor background-execution contract

HaxLab data-pipeline auditors are integrity gates. Their result must be owned by the foreground exact-head job that invoked them; an auditor must not detach work that can outlive the audit call, reorder evidence handling, or report success before concurrent work has finished.

## Required invariant

Python modules recursively named `*_audit.py` below `src/haxlab/ingestion/`, `src/haxlab/learning/` and `src/haxlab/runtime/` must not create hidden/background execution through:

- `threading.Thread`, `threading.Timer`, or `_thread.start_new_thread`;
- `multiprocessing.Process`, pools, or context-owned workers;
- `concurrent.futures.ThreadPoolExecutor` or `ProcessPoolExecutor`;
- detached asyncio scheduling through task creation, futures, thread/executor handoff, loop callbacks/readers/writers, or `TaskGroup`;
- direct process-spawn primitives such as `os.fork`, `forkpty`, and `posix_spawn*`.

Import aliases, assignment aliases, constant-`getattr(...)` spellings, loop/context owner aliases, annotated assignments and relevant thread/executor subclasses are covered.

Ordinary synchronous execution, coroutine definitions, `asyncio.run(...)`, and lock construction remain allowed. Foreground blocking/non-interactivity is governed separately by #223/#224.

## Boundary

This contract is additive-only. It changes no auditor implementation and does not touch producers, runtime state/schema, scanner, archive, analyzer, learning outputs, evaluation code, models, champion state, or canonical CI.

It is separate from the data-pipeline contracts for foreground liveness, read-only behavior, network/subprocess hermeticity, safe deserialization, broad-exception handling, ambient determinism, optimization safety, dynamic loading, and process-state isolation.

## Proof

`tests/test_data_pipeline_audit_background_execution_contract.py` recursively scans the current data-pipeline auditor modules with Python ASTs. Self-tests cover thread/process/executor creation, multiprocessing context aliases, detached asyncio scheduling, assignment and constant-`getattr(...)` aliases, relevant subclasses, and the permitted synchronous boundary.

The branch-scoped proof workflow verifies exact checkout and live branch-head identity, precompiles the repository for isolated-import regressions, runs the focused contract, and reruns the adjacent data-pipeline audit contract bundle including the foreground-liveness contract.

Only proof from the final exact branch SHA counts as acceptance evidence.
