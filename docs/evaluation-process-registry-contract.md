# Evaluation process-registry contract

HaxLab evaluation gates must not change process-wide logging or warning registries.

Evaluation functions are reused inside longer-lived validation, calibration and promotion processes. A gate that changes global logger handlers, logger levels, logging disable state, the logger class, warning filters, warning capture, or warning-filter context can make a later gate behave differently solely because an earlier gate ran first. That violates evaluation isolation even when ordinary module globals remain immutable.

The additive contract in `tests/test_evaluation_process_registry_contract.py` therefore rejects:

- process-wide logging configuration via `basicConfig`, `disable`, `setLoggerClass`, `shutdown`, and `captureWarnings`;
- logger/root registry mutation through `addHandler`, `removeHandler`, `addFilter`, `removeFilter`, and `setLevel` on objects obtained from `logging.getLogger(...)` or `logging.root`;
- warnings filter/registry mutation through `simplefilter`, `filterwarnings`, `resetwarnings`, and `catch_warnings`;
- direct imports, module aliases, simple/annotated/named aliases, bound mutator aliases, and constant `getattr(...)` spellings of those operations.

The contract intentionally preserves read-only logger lookup and metadata inspection, normal diagnostic emission such as `logger.info(...)` and `warnings.warn(...)`, and mutation of a handler object that has not been installed into a process-wide logger registry.

This is a validation-only boundary. It changes no evaluation thresholds, evidence schema, scenario source, calibration input, policy, model, champion pointer, promotion state, or canonical Arena-v2 workflow.

## Relationship to adjacent contracts

This scope is deliberately separate from:

- process-global OS/interpreter mutation (#200/#201);
- module-global mutable containers (#167/#168);
- hidden background execution (#211/#212);
- mutable defaults (#261/#262);
- hidden memoization (#323/#326);
- shared class state (#329/#335);
- function-attribute state (#342/#344);
- context/thread-local persistent state (#348/#351).

Acceptance requires an exact-head self-hosted proof plus the adjacent canonical Arena-v2 evaluation regression suite.
