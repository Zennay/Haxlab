# Skill auditor core-policy coverage

The HaxLab skill leaderboard audit is part of the data pipeline: it verifies published leaderboard evidence before downstream use. Several older cross-cutting audit safety contracts were introduced when audit modules lived only under ingestion, learning, and runtime, so their own discovery roots do not include `src/haxlab/skill`.

This additive bridge closes that coverage gap without changing those established owner files or duplicating their rule logic.

## Contract

Every `src/haxlab/skill/**/*_audit.py` module is discovered recursively. The bridge loads the **current repository implementations** of the integrated audit-policy scanners and applies them to each skill auditor.

The current bridge covers:

- process-exit callback registry state;
- hidden background execution;
- ambient nondeterminism;
- fail-closed exception handling;
- module-global state mutation;
- network/process hermeticity;
- process-wide instrumentation state;
- dependency-layer boundaries;
- liveness;
- logging configuration state;
- mmap write access;
- mutable default arguments;
- native FFI loading;
- process termination;
- read-only I/O;
- runtime stdout/stderr purity;
- safe deserialization;
- SQLite extension loading;
- Python warning-state mutation.

This means policy fixes made in the canonical scanner tests are automatically consumed by the skill coverage bridge instead of being copied into a second implementation.

A manifest drift guard also compares the bridge mapping with every integrated `tests/test_data_pipeline_audit_*.py` policy file. When a new core audit policy lands on `main`, this branch must explicitly map its live scanner before it can pass, preventing silent policy omission.

## Scope boundary

The bridge is validation-only. It does not modify `src/haxlab/skill/leaderboard_audit.py`, the existing policy tests, skill estimation/scoring, ingestion, runtime, learning producers, evaluation, models, champion pointers, thresholds, or live state.

The process-instrumentation policy from merged #530, the atexit-state policy from merged #534, and logging-state policy from merged #524 are included on the current base.

## Validation discipline

Before integration this branch must be reconciled onto then-current `main`, run through normal exact-head HaxLab CI, and be rechecked for overlap with any newly integrated audit-policy lanes.
