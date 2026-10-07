# Arena metric-bounds workflow provenance contract

The Arena v2 metric-bounds validator publishes exact-head evaluation evidence. Its checkout must be immutable and its resulting repository identity must be verified before bounded-metric regressions execute.

The workflow pins `actions/checkout` to reviewed immutable v4 commit `11d5960a326750d5838078e36cf38b85af677262`, preserves `ref: ${{ github.sha }}` and `clean: true`, disables persisted checkout credentials, and fails immediately after checkout unless `git rev-parse HEAD` equals the immutable GitHub event SHA.

A focused provenance contract runs before the existing closed-loop Arena, multisource-duel, and multisource-suite regressions. The staging branch is intentionally absent from the push trigger, so this preparation does not consume the shared HaxLab runner.

This lane changes validation provenance only. It does not change metric bounds, frozen policy thresholds, Arena product logic, models, champion pointers, promotion state, calibration inputs, or the canonical Arena integration workflow. Exact-head self-hosted proof remains required before PR/review.
