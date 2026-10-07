# Calibration run outcome contract

## Purpose

A red GitHub Actions calibration job is not automatically evidence that a HaxLab
challenger failed the Arena-v2 gate. Infrastructure shutdown, cancellation,
preflight failure, malformed evidence and deterministic policy rejection are
different states and must remain distinguishable.

This contract is diagnostic-only. It cannot authorize a merge, promotion,
champion-pointer mutation or threshold change.

## Canonical semantics

The classifier reads the normalized GitHub job payload for the canonical
`calibrate` job and, optionally, the decoded job log.

Classifications are fail-closed:

- `green` — every required calibration phase, pointer invariant and evidence
  step completed successfully. This is the only class that sets
  `promotion_evidence_valid=true`.
- `interrupted` — the batch, summary or downstream evidence path was cancelled,
  timed out or became stale. This is never a model rejection.
- `validation_failed` — exact checkout, frozen Candidate-D checkout,
  implementation validation or input preparation failed. This is never a model
  rejection.
- `execution_failed` — the full paired batch itself failed rather than being
  cancelled/timed out. The job summary alone cannot prove that this was a
  challenger-policy rejection.
- `summary_failed` — the paired batch completed but the deterministic summary
  step failed without an explicit calibration-gate rejection marker.
- `evaluation_rejected` — the paired batch completed, the summary step failed,
  and the decoded job log contains the exact message emitted by the deterministic
  calibration gate: `frozen policy validation failed:`. Only this classification
  sets `model_rejection=true`.
- `evidence_failed` — the evaluation summary passed but pointer/evidence
  publication failed. The model result cannot be promoted because the immutable
  proof is incomplete.
- `incomplete` / `invalid` — nonterminal or malformed job evidence.

Unknown, duplicate or malformed required steps fail closed.

## 2026-10-07 incident

Canonical run `37555481447`, job `112580599451`, reached the full paired
calibration batch after all preflight phases passed. During the weak-zero batch a
Node Arena process was killed, followed immediately by the GitHub runner shutdown
signal. The batch step concluded `cancelled`; summary, pointer verification and
evidence collection were skipped.

Under this contract that run is `interrupted`, with
`promotion_evidence_valid=false` and `model_rejection=false`. It therefore
does not reject Candidate D and it cannot supply promotion evidence.

## CLI

```bash
PYTHONPATH=src python -m haxlab.evaluation.calibration_run_outcome job.json
```

Pass `--job-log job.log` when a failed summary should be checked for the
deterministic gate-rejection marker. Pass `--require-green` when a caller should
exit non-zero for every non-green diagnostic class.

## Ownership boundary

This lane does not modify the calibration workflow, calibration gate/resume,
promotion, multisource, scenario source, resync guard, evaluation receipt/finalizer,
models, thresholds or champion pointers. Future integration should consume this
classifier only as diagnostic state; mandatory exact-head gate receipts remain the
authority for promotion eligibility.


## Focused exact-head validation

The branch carries `.github/workflows/calibration-run-outcome-contract-validation.yml`.
It checks out and asserts the exact event SHA, builds an isolated environment, compiles
the diagnostic contract and runs only `tests/test_calibration_run_outcome.py` on
the self-hosted HaxLab runner. The workflow is intentionally branch-scoped and its
push job runs only for an explicit `[calibration-outcome-proof]` commit marker.
