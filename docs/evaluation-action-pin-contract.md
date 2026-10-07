# Evaluation action pin contract

HaxLab evaluation evidence is bound to an exact repository commit. The workflow implementation that materializes that commit must be reproducible too, and the checked-out worktree must be verified before tests begin.

## Invariants

`.github/workflows/arena-v2-evaluation-validation.yml` must:

1. reference official GitHub Actions by immutable 40-character commit SHA, not a moving major/minor tag;
2. checkout the event's exact `github.sha`;
3. verify `git rev-parse HEAD == github.sha` before creating the test environment or executing evaluation regressions.

The reviewed `actions/checkout` v4 tag resolved to:

`11d5960a326750d5838078e36cf38b85af677262`

The workflow keeps a trailing `# v4` comment for readability, but the executable reference is the immutable commit.

## Why this is validation-critical

A branch or candidate SHA can be rerun days later. With `actions/checkout@v4`, that rerun may execute different action code even though HaxLab source is unchanged. A green rerun would therefore not be fully reproducible from the HaxLab SHA alone.

Even with an immutable action implementation, exact-head evidence should fail closed if the resulting worktree does not equal the requested event SHA. The explicit post-checkout identity check establishes that boundary before any test evidence is produced.

These invariants close the provenance gap without changing Arena thresholds, evaluation semantics, models, champion state, or frozen inputs.

## Change policy

Advance the action pin only through a reviewed change that records the new immutable action commit. Any checkout/provenance change must keep `tests/test_evaluation_workflow_action_pins.py` green and preserve verification before test setup.
