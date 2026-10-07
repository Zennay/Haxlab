# Evaluation foreground-liveness contract

HaxLab evaluation gates must be usable as deterministic library code and must not
wait for a person or park the host process on explicit blocking primitives.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
introduce:

- interactive console prompting via `input()`;
- explicit sleeps via `time.sleep` or `asyncio.sleep`;
- indefinite process parking via `signal.pause`;
- recognized synchronization waits/acquires from `threading` or
  `multiprocessing`;
- blocking queue receives/joins from `queue` or `multiprocessing`;
- blocking `concurrent.futures.Future.result()` / `.exception()` waits.

Aliases, assignment aliases and constant-`getattr(...)` spellings are covered.
Ordinary dictionary `.get()`, pure local computation and custom methods merely
named `wait` remain allowed.

## Boundary

This contract is intentionally separate from:

- #92, which owns canonical workflow runtime-budget/collection sequencing;
- #147/#148, which owns ambient nondeterminism such as clocks, randomness and
  environment entropy;
- #151/#153, which owns network and child-process hermeticity;
- #200/#201, which owns process-global state mutation;
- #211/#212, which owns hidden background execution;
- #220, which owns evidence-object immutability bypasses.

This lane protects only foreground liveness and non-interactivity. It changes no
evaluation implementation, model, threshold, champion pointer or canonical
Arena-v2 workflow.

## Proof

`tests/test_evaluation_liveness_contract.py` recursively scans the evaluation
package with Python ASTs. Its self-tests cover direct calls, import aliases,
assignment aliases, constant `getattr`, synchronization objects, futures and
blocking queues while freezing allowed dictionary access and unrelated custom
methods.

The branch-scoped proof workflow verifies the exact SHA is still the live branch
head, compiles the package and contract, runs the focused liveness suite, then
runs the 12 adjacent canonical Arena-v2 evaluation regressions that are not
blocked by owner-reserved #92.

A green result is validation evidence only. It does not authorize PR #19 merge
or champion promotion.
