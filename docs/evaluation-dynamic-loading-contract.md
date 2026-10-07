# Evaluation dynamic module-loading contract

HaxLab's evaluation package has a static import boundary so promotion decisions
cannot silently couple to mutable runtime/control-plane state. That boundary
would be incomplete if evaluation code could resolve and execute a module by
string or filesystem path at runtime.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
perform dynamic module/code loading.

The contract rejects calls to:

- builtin `__import__`, including aliases imported from `builtins`;
- `importlib.import_module`;
- `importlib.util.spec_from_file_location`;
- `importlib.util.module_from_spec`;
- executable import loaders such as `SourceFileLoader`,
  `SourcelessFileLoader`, and `ExtensionFileLoader`;
- `runpy.run_module` and `runpy.run_path`;
- `pkgutil.resolve_name`.

Ordinary static imports remain allowed. Read-only discovery helpers that do not
load/execute a module, such as `importlib.util.find_spec`, are also outside
this narrow prohibition.

## Why this is separate from the static import contract

The control-plane import contract can see statements such as
`import haxlab.runtime`, but a call like
`importlib.import_module(name_from_payload)` has no statically declared target.
It can therefore bypass a static dependency review and execute arbitrary module
initialization in the gate process.

Keeping dynamic loading out of evaluation preserves a reviewable dependency
graph and prevents string/file-driven module execution from becoming an
alternate control-plane path.

## Proof

`tests/test_evaluation_dynamic_loading_contract.py` recursively scans the
production evaluation tree with Python ASTs and regression-tests builtin,
aliased, importlib, loader, runpy, and pkgutil loading paths.

The branch-scoped self-hosted proof verifies the immutable event SHA, compiles
the evaluation package and contract, runs the focused regression, and emits
`HAXLAB_EVALUATION_DYNAMIC_LOADING_RESULT=green` only on success.

A green result is narrow validation evidence only. It does not authorize
canonical Arena-v2 PR #19 merge or champion promotion.
