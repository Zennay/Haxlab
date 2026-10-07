# Data-pipeline audit logging-state contract

HaxLab data-pipeline auditors are evidence verifiers. They may run in the same Python process as other validation code, so they must not change process-global logging configuration as a side effect of inspecting evidence.

## Protected surface

The contract scans every `*_audit.py` module under `src/haxlab`, including the skill leaderboard auditor. This closes the historical coverage gap where several older audit-policy contracts only enumerate ingestion, learning, and runtime roots.

It rejects:

- module-level logging configuration such as `logging.basicConfig()`, `logging.disable()`, `logging.shutdown()`, logger/log-record factory replacement, warning capture, and level registration;
- `logging.config` configurators such as `dictConfig()`, `fileConfig()`, `listen()`, and `stopListening()`;
- logger handler/filter/level mutation through direct calls, imported aliases, assignment aliases, or constant-string `getattr(...)`;
- dynamic `getattr` on the logging module or canonical logger objects, because the selected capability cannot be proven read-only statically;
- direct mutation of logger configuration attributes such as `disabled`, `handlers`, `filters`, `level`, `parent`, and `propagate`;
- mutation of selected module-global logging attributes and wildcard logging imports that would make the static capability boundary ambiguous.

Normal evidence emission remains permitted. Auditors may obtain loggers, call ordinary emission methods, inspect effective levels/handlers, use constant-string read-only `getattr`, and read the current logging factory/class without mutating them.

## Why this matters

A verifier that reconfigures logging can suppress, redirect, duplicate, or reshape evidence emitted by later validation in the same interpreter. That makes validation behavior depend on execution order rather than only on explicit inputs.

The contract is additive and does not modify any auditor or producer implementation. It exists to keep audit execution observational with respect to host-process logging state.

## Validation

The dedicated `Data-pipeline audit logging-state proof` workflow checks out the exact branch SHA on the self-hosted HaxLab runner, verifies that the checkout is still the live branch head, compiles repository Python, runs the focused contract, and runs adjacent audit-state contracts. Normal HaxLab pull-request CI remains required before integration.
