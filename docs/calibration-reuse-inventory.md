# Calibration reuse inventory

Issue #420 adds a read-only recovery diagnostic for Arena-v2 calibration batches.

## Purpose

A self-hosted runner interruption can leave a calibration output directory with a mixture of reusable and missing source results. The calibration workflow already validates a result immediately before reuse through `haxlab.evaluation.calibration_resume`, but without a separate inventory command an operator has to enter the expensive batch loop or inspect logs to learn the reusable coverage.

`python -m haxlab.evaluation.calibration_inventory` classifies the complete frozen calibration grid before a recovery rerun:

- labels: `champion-self`, `candidate-d`, `weak-zero`;
- sources: `01`, `02`, `03`;
- total expected results: 9.

Every existing result is delegated to `calibration_resume.reusable_result_or_reasons` with the exact challenger, champion, partner, stadium, scenario and evaluation parameters supplied by the caller. The inventory does not weaken or replace the reuse validator.

## Fail-closed behavior

A row is reusable only when the existing validator returns reusable with no rejection reasons. Missing/corrupt result files and unavailable bound inputs are represented as blocked rows. Expected filesystem/type/value failures from the delegated validator are converted into a deterministic `validator_error:<ExceptionType>` row instead of aborting the whole inventory.

The command never copies or rewrites calibration results, regenerates gate decisions, mutates champion pointers, changes thresholds, or authorizes promotion.

## CLI

The command requires the existing result root, frozen source root, all three challenger identities, partner models and the exact evaluation request:

```bash
python -m haxlab.evaluation.calibration_inventory \
  /path/to/calibration-output \
  --source-root /path/to/frozen-sources \
  --champion /path/to/champion.json \
  --candidate-d /path/to/candidate-d.json \
  --weak-zero /path/to/weak-zero.json \
  --partner-model /path/to/champion.json \
  --partner-model /path/to/partner-b.json \
  --partner-model /path/to/partner-c.json \
  --seconds 30 \
  --sample-every 6 \
  --max-scenarios 16 \
  --plug-repeats 1 \
  --seed 1337
```

The JSON report includes aggregate reusable/blocked counts, per-label counts and all nine ordered source rows with rejection reasons.

By default the command exits 0 when inventory generation itself succeeds, even when some rows are blocked. Add `--require-complete` when automation should return non-zero unless all 9/9 rows are reusable.

## Scope boundary

This is diagnostic evidence only. It does not change the canonical calibration workflow owned by #408/#410 and #411, the run-outcome classifier owned by #105/#107, or validation behavior inside `calibration_resume.py` owned by PR #76.
