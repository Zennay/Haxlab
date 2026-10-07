# Evaluation background-execution contract

HaxLab's evaluation package is the decision boundary for immutable Arena-v2
evidence. Those validators are expected to run synchronously inside the
workflow/runner that owns the exact-head proof. Evaluation library code must not
silently detach work that can outlive the gate call or reorder evidence handling.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
create hidden/background execution through:

- `threading.Thread` or `threading.Timer`;
- `_thread.start_new_thread`;
- `multiprocessing.Process`, pools, or spawn-context workers;
- `concurrent.futures.ThreadPoolExecutor` or `ProcessPoolExecutor`;
- detached asyncio scheduling via `create_task`, `ensure_future`,
  `run_coroutine_threadsafe`, `to_thread`, loop task/executor APIs,
  deferred callback APIs (`call_soon`, `call_later`, `call_at`,
  `add_reader`, `add_writer`) or `TaskGroup`;
- direct process-spawn primitives such as `os.fork`, `forkpty` and
  `posix_spawn*`.

Ordinary synchronous execution remains allowed. Coroutine definitions,
`asyncio.run(...)`, `asyncio.sleep(...)` and synchronization primitives such
as `threading.Lock` are not rejected because they do not detach work by
themselves.

## Boundary

This lane is intentionally separate from existing evaluation-validation work:

- #151/#153 owns network and subprocess hermeticity, including asyncio child
  processes;
- #200/#201 owns mutation of process-global state;
- #208/#209 owns host-process termination;
- ambient determinism owns clocks, randomness and environment-dependent
  decision inputs.

This contract only prevents evaluation library code from starting concurrent or
background execution that can escape the synchronous exact-head gate boundary.
It changes no Arena implementation, policy threshold, model, champion pointer or
canonical integration workflow.

## Proof

`tests/test_evaluation_background_execution_contract.py` recursively scans the
evaluation package with Python ASTs. Self-tests cover direct imports, aliases,
assignment aliases, constant-`getattr(...)` bypasses, multiprocessing contexts,
executor construction and detached asyncio scheduling.

The branch-scoped self-hosted workflow verifies exact checkout, compiles the
evaluation package and contract, runs the focused contract, then runs the same
12 adjacent canonical Arena-v2 evaluation regression files used by the existing
validation bundle (excluding owner-reserved blocker #92).

A green result is narrow validation evidence only. It does not authorize PR #19
merge or champion promotion.
