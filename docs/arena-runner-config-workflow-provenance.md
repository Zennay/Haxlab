# Arena runner-config workflow provenance contract

The Arena runner-config integrity workflow produces evaluation evidence for the closed-loop JS runner and adjacent Python consumers. Its checkout implementation and source identity must be immutable and verified before any syntax check, runner regression, environment setup, or consumer regression runs.

## Invariants

`.github/workflows/arena-runner-config-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before Node syntax, JS runner regressions, Python environment setup, or adjacent consumer regressions;
5. preserve read-only permissions, the `[self-hosted, haxlab]` runner, 20-minute timeout, historical push trigger, manual trigger, and existing JS/Python validation scope.

## Rationale

A mutable `actions/checkout@v4` tag can resolve to different action code when the same HaxLab revision is rerun later. Persisted checkout credentials also leave an unnecessary repository token in git configuration. Recording HEAD only after validation is insufficient because provenance-invalid evidence may already have been produced.

This hardening changes no Arena runner implementation, consumer semantics, thresholds, models, champion pointer, promotion state, or canonical Arena-v2 integration code.

## Proof sequencing

`.github/workflows/arena-runner-config-workflow-provenance-proof.yml` is deliberately `workflow_dispatch`-only. It requires the dispatched SHA to equal both checked-out HEAD and the live remote branch head, then runs the focused provenance contract before the existing JS runner and adjacent Python consumer regressions.

Keep that proof undispatched while higher-priority canonical evaluation sequencing owns the shared HaxLab runner. A later green proof is narrow source-provenance evidence only.
