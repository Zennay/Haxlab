# Data-pipeline audit process-instrumentation isolation

HaxLab data-pipeline auditors are evidence verifiers. They may inspect runtime instrumentation state, but they must not install or change process-wide instrumentation that survives beyond the current audit call.

## Scope

The contract recursively scans every `*_audit.py` module below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

It is additive validation only. It does not modify auditor, producer, runtime, learning, evaluation, model, or champion implementation.

## Forbidden mutations

Audit modules must not invoke:

- `sys.addaudithook(...)`;
- current-thread trace/profile setters: `sys.settrace(...)` and `sys.setprofile(...)`;
- mutating `faulthandler` APIs: `enable`, `disable`, `register`, `unregister`, `dump_traceback_later`, `cancel_dump_traceback_later`;
- `tracemalloc.start`, `stop`, `reset_peak`, or `clear_traces`;
- threading-wide trace/profile setters: `threading.settrace`, `setprofile`, `settrace_all_threads`, `setprofile_all_threads`;
- mutating `sys.monitoring` APIs: `use_tool_id`, `free_tool_id`, `register_callback`, `set_events`, `set_local_events`, or `restart_events`.

The contract resolves normal imports, direct imports, import aliases, assignment/chained/tuple aliases, and constant-string `getattr(...)` indirection. Wildcard imports from the tracked instrumentation modules are rejected because they make mutator provenance statically ambiguous.

## Allowed behavior

Read-only instrumentation inspection remains valid, including:

- `faulthandler.is_enabled()`;
- `tracemalloc.is_tracing()` and `tracemalloc.get_traced_memory()`;
- `sys.gettrace()` and `sys.getprofile()`;
- `threading.gettrace()` and `threading.getprofile()`;
- `sys.monitoring.get_events(...)` and other non-mutating monitoring reads.

## Why this matters

These APIs change persistent interpreter or thread instrumentation. If an auditor installs an audit hook, current-thread trace/profile callback, threading-wide callback, fault handler, allocation tracer, or monitoring callback, later validation can observe different behavior solely because another audit ran first. Exact-head evidence must depend on explicit repository and data inputs, not hidden process instrumentation left behind by a verifier.

## Proof

Focused regression:

`tests/test_data_pipeline_audit_instrumentation_state_contract.py`

Exact-head self-hosted workflow:

`.github/workflows/data-pipeline-audit-instrumentation-state-proof.yml`

Integration requires the focused proof and normal HaxLab CI to be terminal green on the same immutable candidate head.
