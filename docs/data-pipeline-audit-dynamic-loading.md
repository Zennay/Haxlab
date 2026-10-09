# Data-pipeline audit dynamic-loading contract

HaxLab data-pipeline auditors are integrity verifiers. Their executable dependency graph should remain statically reviewable: evidence must not choose or load Python code at runtime.

This validation contract recursively covers every `*_audit.py` module below ingestion, learning, and runtime.

## Rejected runtime loading

The contract rejects:

- builtin `__import__`;
- `importlib.import_module` and `importlib.reload`;
- executable importlib spec/module paths such as `spec_from_file_location` and `module_from_spec`;
- loader execution through `exec_module` or legacy `load_module`;
- `runpy.run_module` and `runpy.run_path`;
- `pkgutil.resolve_name` dynamic symbol resolution.

Imported aliases are resolved, so renaming these APIs does not bypass the contract.

## Allowed static behavior

Normal static imports remain allowed. Read-only import metadata discovery such as `importlib.util.find_spec` and `importlib.metadata.version` is also allowed because it does not execute a dynamically selected module.

This contract is additive validation only. It does not modify audit implementations, producers, runtime state, evaluation code, models, or champion state.
