# Evaluation additive-contract compatibility proof v8

Proof-only carrier for issue #367.

Canonical Arena-v2 base:
- `ee5ab508e9d8504de12b7cbb5fdeca43a58ae1a4`

This generation carries the repaired v7 set of 21 current pinned owner heads and adds six newer evaluation-validation owners:
- #344 function-attribute state: `db1061c2d3a6ede6bbae5c2d7a184361cc96d2c5`
- #351 context-local state: `bc822599137f9df6abadb8700cd6e942f2ffd6c7`
- #354 module-object state: `ff5e128581f676dc1b939db6acc7e4361ff12781`
- #356 logging/warnings registry state: `f43870b701cf63d5eeab1764c6d37cc39a2835df`
- #357 process registry mutation: `ad19f1c36ada3e42d4eba5458cb2b9aa668ce2d6`
- #359 atexit registry state: `cfb6cc827b078fff08e7999b9f311fe76acd48e8`

The workflow is staged with a safe branch-push trigger plus `workflow_dispatch`. The self-hosted `prove` job runs only for a manual dispatch or when the triggering commit message contains `[run-v8]`; ordinary preparation pushes are skipped before runner allocation while canonical calibration attempt 2 is active.

At execution it:
1. checks out untouched canonical Arena-v2;
2. verifies all 27 owner branches still match the pinned exact heads;
3. materializes only additive contract test/workflow files into the runner workspace;
4. compiles and runs the combined 27-contract stack;
5. runs the adjacent canonical Arena-v2 regression bundle;
6. emits `HAXLAB_EVALUATION_CONTRACT_STACK_COMPAT_V8=green` only after full success.

This is compatibility evidence only. It does not replace owner-specific exact-head acceptance and cannot authorize canonical resync/merge or champion promotion. No evaluation product implementation, thresholds, policies, model artifacts, scenario/calibration evidence, champion pointers, promotion state or canonical workflow is modified.
