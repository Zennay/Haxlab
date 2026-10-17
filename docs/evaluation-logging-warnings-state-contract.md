# Evaluation logging and warnings state contract

HaxLab evaluation gates must remain repeatable inside a long-lived Python process. A gate may inspect diagnostics and emit ordinary log or warning messages, but it must not reconfigure process-lifetime logging or warnings registries.

This contract recursively scans `src/haxlab/evaluation/**/*.py` and rejects:

- global logging reconfiguration through `logging.basicConfig`, `logging.disable`, `logging.captureWarnings`, `logging.shutdown`, and `logging.config` configuration entry points;
- mutation of root or named logger registry state through handler/filter attachment, level changes, propagation/disabled flags, or direct handler/filter collection replacement;
- global warning-filter mutation through `warnings.simplefilter`, `warnings.filterwarnings`, `warnings.resetwarnings`, or direct mutation of `warnings.filters`;
- direct-import aliases, module aliases, simple assignment aliases, and constant-`getattr(...)` spellings of the same mutation surfaces.

Read-only diagnostics remain valid. Evaluation code may call ordinary logger emission methods, inspect logger state, inspect `warnings.filters`, and use `warnings.catch_warnings(...)` as a scoped context that restores the global filter registry after exit.

## Why this is separate from process-state hardening

The existing process-state contract covers operating-system and interpreter-wide state such as cwd, environment, signal handlers, locale, `sys.path`, `sys.modules`, tracing, and recursion settings. Python logging and warnings maintain their own process-lifetime registries, so they require a distinct guard.

This is an additive validation contract only. It does not alter evaluation thresholds, policies, calibration/scenario evidence, model state, champion pointers, promotion decisions, or canonical Arena-v2 workflow behavior.
