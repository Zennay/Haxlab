# Evaluation interpreter scheduling limits

HaxLab evaluation code runs inside long-lived validation processes on the shared self-hosted runner. A gate must not leave Python interpreter execution limits changed for a later gate.

This additive contract recursively scans `src/haxlab/evaluation/**/*.py` and rejects mutation through:

- `sys.setrecursionlimit(...)`;
- `sys.setswitchinterval(...)`;
- `sys.set_int_max_str_digits(...)`;
- setter-form `threading.stack_size(...)`.

The zero-argument `threading.stack_size()` getter and read-only `sys.getrecursionlimit()`, `sys.getswitchinterval()`, and `sys.get_int_max_str_digits()` remain allowed.

The AST guard resolves ordinary module/import aliases, assignment aliases, and constant-string `getattr(...)` calls. Function parameters shadow inherited aliases, avoiding false positives on unrelated objects.

## Ownership boundary

Issue #536 owns only this additive interpreter-limit contract, this document, and its manual proof workflow. It does not modify evaluation implementation, thresholds, evidence formats, model/champion/promotion state, calibration or multisource inputs, the Arena integration validator, or the #100/#436 owner surfaces.

This is distinct from the existing process-global-state, instrumentation, garbage-collector, host-resource scheduling, logging/warnings, atexit, and numeric-runtime contracts. A green proof establishes only this narrow validation invariant and does not authorize Arena integration or champion promotion.
