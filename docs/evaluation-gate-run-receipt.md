# Arena-v2 exact-head gate receipt

This contract prevents a collection of individually valid but mismatched GitHub
Actions runs from being treated as merge or promotion authorization.

## Mandatory set

A receipt is green only when exactly these three workflows all completed with
`conclusion=success` on the same immutable lowercase 40-character commit SHA:

- `.github/workflows/closed-loop-arena-v2-calibration.yml`
- `.github/workflows/multisource-suite-v2.yml`
- `.github/workflows/ci.yml`

The validator is intentionally offline. An orchestration step fetches GitHub run
metadata, writes a small JSON request, and invokes:

```bash
python -m haxlab.evaluation.gate_run_receipt request.json --output receipt.json
```

The request has this shape:

```json
{
  "exact_head": "0123456789abcdef0123456789abcdef01234567",
  "runs": [
    {
      "repository_full_name": "Zennay/Haxlab",
      "gate": "calibration",
      "run_id": 123,
      "run_attempt": 1,
      "workflow_path": ".github/workflows/closed-loop-arena-v2-calibration.yml",
      "workflow_name": "Closed-Loop Arena v2 Frozen Policy Validation",
      "head_sha": "0123456789abcdef0123456789abcdef01234567",
      "status": "completed",
      "conclusion": "success",
      "event": "workflow_dispatch",
      "html_url": "https://github.com/Zennay/Haxlab/actions/runs/123"
    }
  ]
}
```

All three mandatory rows must be present exactly once. Missing, duplicated,
queued/running, failed/cancelled, wrong workflow path/name, missing or mismatched
canonical repository identity, canonical Actions URL, reused run IDs,
malformed/non-positive rerun attempts, workflow-specific event violations,
malformed native types, non-native JSON shapes, unknown top-level/run fields,
abbreviated/uppercase SHAs, or cross-SHA evidence fail closed.

The emitted JSON uses schema
`haxlab-evaluation-gate-run-receipt-v1` and is canonicalized with sorted keys
and stable gate ordering. The receipt says only `mandatory_gates_green=true`;
it deliberately does **not** claim that a branch is ready to merge or promote,
because current-main ancestry/resync and other integration conditions are outside
this validator. A historical green receipt is evidence for its exact head only;
after a current-main resync a fresh three-gate receipt is required.

This module does not change champion pointers, thresholds, model weights,
calibration inputs, scenario selection, or workflow dispatch behavior.


## Output publication safety

When `--output` is used, the receipt is written to a temporary file in the
destination directory, flushed and fsynced, then atomically replaced into the
requested path. If parsing, validation, writing, or replacement fails, any
pre-existing output receipt is removed so stale green evidence cannot survive a
failed revalidation.


## GitHub API normalization boundary

For finalizer code that already fetched canonical GitHub Actions run payloads,
use `validate_github_gate_runs(raw_runs, exact_head=...)`. The mapping keys are
the three mandatory gate names. The normalizer derives `repository_full_name`
from the nested GitHub `repository.full_name` field and copies only the exact
receipt surface; unrelated API fields are discarded before strict validation.
Missing/unknown gates or malformed repository payloads fail closed.


## Exact-head runner proof

`.github/workflows/evaluation-gate-run-receipt-validation.yml` supports manual
dispatch and a tightly gated branch push. Ordinary branch updates create no
self-hosted work because the validation job only runs for `workflow_dispatch`
or when the pushed commit message starts with `[receipt-proof]`. This avoids
depending on a branch-only workflow being dispatchable before it exists on
`main`.

When the shared HaxLab runner is free, push one explicit proof-marker commit to
the receipt branch (for example `[receipt-proof] Validate exact receipt head`).
For manual dispatch, pass the exact 40-character lowercase target SHA as required
`expected_sha`. In both modes the workflow derives an immutable expected SHA,
rejects a mismatch before setup, checks out `github.sha`, builds an isolated
environment, compiles the receipt boundary/tests, runs only the focused
regressions, and records the exact checked-out SHA. No pull-request trigger is
defined.


### Direct raw-API CLI mode

The CLI request accepts exactly one evidence representation. Existing callers
may use normalized `runs`; finalizer/orchestration code may instead place the
three unmodified GitHub workflow-run API objects under `github_runs`, keyed by
`calibration`, `haxlab_ci`, and `multisource`:

```json
{
  "exact_head": "0123456789abcdef0123456789abcdef01234567",
  "github_runs": {
    "calibration": {"id": 123, "repository": {"full_name": "Zennay/Haxlab"}},
    "haxlab_ci": {"id": 124, "repository": {"full_name": "Zennay/Haxlab"}},
    "multisource": {"id": 125, "repository": {"full_name": "Zennay/Haxlab"}}
  }
}
```

The abbreviated objects above show the shape only; real payloads must contain
all GitHub run fields required by the strict normalizer. Supplying both
`runs` and `github_runs`, or neither, fails closed.
