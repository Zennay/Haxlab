# Read-only M0 import snapshot comparison

`tools/m0_import_snapshot_compare.py` answers a narrow data-pipeline question:
**did a second M0 import publication change any of the five derived artifact
bytes, and how did its manifest counts change?**

The utility does not execute an import, write to either snapshot, rebuild
artifacts, read raw Discord/replay inputs, query VPS state, or change models,
thresholds, evaluation, champion or promotion state. It supplements (and does
not replace) the existing M0 generation, source-receipt and cross-artifact
auditors. Use their proofs to establish source/provenance validity.

## Use

```bash
python tools/m0_import_snapshot_compare.py /path/to/import-before /path/to/import-after
python tools/m0_import_snapshot_compare.py /path/to/import-before /path/to/import-after --fail-on-drift
python -m pytest -q tests/test_m0_import_snapshot_compare.py
```

Both inputs must be **completed, immutable M0 import output directories**.
The comparator requires exactly the standard five artifact filenames to
exist as readable regular files: `duplicates.json`, `manifest.json`,
`matches.jsonl`, `replays.json`, `reports.json`. It rejects a symlink
snapshot root, symlink artifact, missing artifact, non-regular artifact,
changed file identity during its read, malformed manifest, duplicate JSON
manifest keys, non-finite JSON constants, non-native/negative counters,
incoherent replay counts and impossible match counts. It reads regular files
without following their final symlink component.

## Interpretation

The stdout JSON contains:

- `identical`: whether **all five artifact SHA-256 digests** match.
- `changed_artifacts`: sorted canonical artifact filenames with byte drift.
- `count_delta`: changed manifest counters as **after minus before**.
- `before` and `after`: individual artifact SHA-256 digests and validated
  manifest counters for reproducible diagnosis.

Byte differences include JSON formatting/order changes; the report
intentionally makes **no** semantic-equivalence claim about changed data.
An unchanged summary count does **not** imply identical records, and even
five matching digests do **not** prove a source is authorized or trusted.
Provenance verification remains a separate gate.

Default report-only execution exits 0 for valid comparisons regardless of
differences. `--fail-on-drift` exits 1 on valid but unequal snapshots.
Malformed or unsafe snapshots exit 2 without printing a success report.
Output is deterministic for the same immutable input bytes and does not
embed host absolute paths, wall-clock timestamps or random identifiers.

This tool is a diagnostic for M0 import determinism and regression triage;
it is **not a promotion or deployment gate**.
