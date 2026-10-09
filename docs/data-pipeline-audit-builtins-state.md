# Data-pipeline auditor builtin namespace isolation

HaxLab audit modules verify external replay, manifest, source-ledger and training evidence. An audit must not alter shared interpreter builtins while other audits may run in the same worker.

## Invariant

The source-only regression checks recursively named `*_audit.py` below `src/haxlab/{ingestion,learning,runtime,skill}` and rejects:

- Assignment, augmented assignment or deletion of `builtins` attributes, direct or aliased.
- Edits to `builtins.__dict__` through subscripts, mapping methods or `operator.setitem/delitem/ior`.
- Reflective `setattr`/`delattr` calls against `builtins`, including import aliases.
- Mutations via constant `getattr`, `vars(builtins)`, method aliases and `__builtins__`.
- Wildcard `from builtins import *` which obscures namespace bindings.

Ordinary reads of `builtins`, pure helpers, local mapping mutations, and copying the builtin namespace for inspection are allowed. This is a **static guard**: it does not execute evidence, apply remediation, or claim immunity to arbitrary obfuscated dynamic Python or C-extension side effects.

## Ownership and safety

The regression is independent of the existing process-state contract (#197: `os`/`sys`/locale/signal/umask), logging, instrumentation, environment and ambient-entropy contracts. It changes no product, model, champion/promotion or frozen artifact paths. Skill auditors are scanned directly; the shared skill-policy bridge is also extended with a live `builtins_state` scanner and smoke case, preserving its complete-manifest invariant.

Run `pytest -q tests/test_data_pipeline_audit_builtins_state_contract.py` for focused checks. The manual-only proof `.github/workflows/data-pipeline-audit-builtins-state-proof.yml` requires an immutable commit and refuses stale branch heads. Do not dispatch or merge it while canonical Arena #100 → #436 owns the serialized HaxLab runner.
