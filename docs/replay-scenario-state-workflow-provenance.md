# Replay scenario-state workflow provenance contract

The replay scenario-state integrity workflow produces evaluation evidence from an exact HaxLab revision and then reads the frozen Arena-v2 scenario set on the HaxLab host. Its checkout implementation and checked-out source identity must therefore be trustworthy before any syntax check, regression, or frozen-data validation runs.

## Invariants

`.github/workflows/replay-scenario-state-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before Node syntax checks, replay-state regressions, adjacent Arena-v2 regressions, or frozen scenario reads;
5. preserve read-only permissions, the `[self-hosted, haxlab]` runner, 20-minute timeout, historical push trigger, manual trigger, existing Node regressions, and the 3-source / 48-scenario frozen-data validation boundary.

## Rationale

A mutable `actions/checkout@v4` tag can resolve to different action code when the same HaxLab revision is rerun later. Persisted checkout credentials also leave an unnecessary repository token in git configuration. Printing HEAD only after all validation does not prevent provenance-invalid evidence from being produced first.

This hardening changes no replay-start implementation, scenario bytes, metric thresholds, model files, champion pointer, promotion state, scenario-source implementation, or canonical Arena-v2 integration code.

## Proof sequencing

`.github/workflows/replay-scenario-state-workflow-provenance-proof.yml` is deliberately `workflow_dispatch`-only. It requires the dispatched SHA to equal both the checked-out HEAD and the live remote branch head, then runs the focused provenance contract before the existing replay-state and adjacent Arena-v2 Node regressions.

Keep that proof undispatched while higher-priority canonical evaluation sequencing owns the shared HaxLab runner. A later green proof is narrow source-provenance evidence only; it does not authorize Arena merge or champion promotion.
