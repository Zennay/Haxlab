# Calibration gate workflow provenance contract

The calibration-gate validator produces exact-head evidence for a fail-closed acceptance boundary. The checkout implementation and checked-out source must be immutable and verified before the validation environment or regressions run.

## Invariants

`.github/workflows/calibration-gate-contract-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before environment setup;
5. preserve read-only permissions, `[self-hosted, haxlab]`, timeout, triggers, calibration-gate compile target, and the existing calibration + closed-loop regression scope.

This changes no calibration-gate logic, threshold, count semantics, model, input, champion pointer, promotion state, or Arena runtime.

## Proof sequencing

`.github/workflows/calibration-gate-workflow-provenance-proof.yml` is manual-only and validates the source-provenance contract plus the adjacent calibration/closed-loop regressions on an exact dispatched/live branch head.

Keep it undispatched while canonical `#100 → #436` sequencing remains owner-held. A later green result is narrow provenance evidence only.
