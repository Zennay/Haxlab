# M0 import failure-stage triage (read-only)

`tools/m0_import_failure_triage.py` summarizes **already recorded failures** in a single M0 `manifest.json`. It never reads the Discord export, opens an HBR2 replay, rebuilds a dataset, writes files, runs a model, or calls the VPS.

### Why this is useful
The canonical M0 importer records `ImportFailure(source, stage, error)` entries for invalid Discord JSON, malformed Discord messages and invalid replay headers. When a batch has many failures, the operator first needs to distinguish these categories **without publishing source filenames, URLs or exception strings**.

### Commands

```bash
python3 tools/m0_import_failure_triage.py /path/to/completed/m0-import
python3 tools/m0_import_failure_triage.py /path/to/completed/m0-import --fail-on-failures
python3 -m unittest discover -s tests -p 'test_m0_import_failure_triage.py' -v
```

The JSON output contains a SHA-256 of **manifest bytes**, the validated count fields, `failure_records`, sorted `by_stage`, the number of unmatched replay/report identifiers and explicit negative authority flags. It never emits individual `source` or `error` values. Known phases: `discord_json`, `discord_messages`, `hbr2_validation`. Unrecognized phases are counted together as `other_stage`; a new importer phase does not cause potentially sensitive labels to be exposed.

It rejects symlinks and nonregular files, missing manifests, oversized manifests (>8 MiB), duplicate JSON keys, NaN/infinite JSON constants, inconsistent counters, duplicate/invalid unmatched identifiers, malformed failure records, and changed file/root identity during descriptor-backed reads. No temporary or cache files are created.

**Exit codes:** 0 means a valid diagnostic (failures may still exist); 1 means valid diagnostic with one or more recorded failures **only with** `--fail-on-failures`; 2 means invalid/untrusted diagnostic input. An empty failure list does not demonstrate a clean corpus.

### Explicit non-goals / ownership
- The M0 manifest does **not** represent the runtime replayer/analyzer backlog or the historical 1,822 replay-processing failures. Do **not** reinterpret the output as a VPS recovery or full 7,775 replay health report.
- It does not prove source receipts, training split isolation, SHA membership, archive validity, the five output artifact bytes, live worker state or deployment readiness. Use existing source-receipt and cross-artifact audits for those independent gates.
- The diagnostic is disjoint from ongoing [#549](https://github.com/Zennay/Haxlab/pull/549) two-publication immutable byte comparator, [#547](https://github.com/Zennay/Haxlab/pull/547) entropy guard, [#548](https://github.com/Zennay/Haxlab/pull/548) builtins guard, and the serialized #100→#436 evaluation runs.
- No frozen evidence, importer runtime logic, shards, model quality thresholds, champion pointers or production service is modified.

**Always:** `diagnostic_only=true`, `raw_inputs_validated=false`, `full_dataset_integrity_proven=false`, `live_runtime_observed=false`, `training_authorized=false`, `promotion_authorized=false`.
