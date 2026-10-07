# Promotion evidence workflow provenance contract

The promotion-evidence validator produces acceptance evidence for an exact HaxLab revision. Checkout implementation and checked-out source identity must therefore be immutable and verified before any validation work runs.

## Invariants

`.github/workflows/promotion-evidence-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before creating the test environment;
5. preserve read-only permissions, the `[self-hosted, haxlab]` runner, timeout, triggers, and the existing promotion + adjacent regression scope.

## Rationale

A mutable `actions/checkout@v4` tag can resolve to different action code when the same HaxLab SHA is rerun later. Persisted checkout credentials also leave an unnecessary repository token in git configuration. Printing HEAD only after all regressions does not prevent provenance-invalid evidence from being produced first.

This hardening changes no promotion decision semantics, thresholds, evidence payloads, models, calibration inputs, champion pointer, or Arena runtime.

## Proof sequencing

`.github/workflows/promotion-evidence-workflow-provenance-proof.yml` is manual-only and requires exact dispatched SHA plus live remote branch-head identity before focused and adjacent promotion regressions.

Keep the proof undispatched while canonical `#100 → #436` sequencing remains owner-held. A later green proof is narrow provenance evidence only.
