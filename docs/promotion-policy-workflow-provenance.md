# Promotion policy workflow provenance contract

The promotion-policy integrity workflow produces validation evidence for an exact HaxLab source revision. Its checkout implementation and resulting worktree must therefore be immutable and verified before any environment setup or regression execution.

## Invariants

`.github/workflows/promotion-policy-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use the reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout the immutable event `github.sha` with a clean worktree and `persist-credentials: false`;
4. prove `git rev-parse HEAD == github.sha` immediately after checkout and before test-environment creation;
5. preserve read-only repository permissions, the `[self-hosted, haxlab]` runner boundary, the existing timeout, and the existing promotion-policy regression scope.

## Why this is validation-critical

A mutable action tag such as `actions/checkout@v4` can resolve to different action code on later reruns of the same HaxLab candidate. Merely printing the resulting repository HEAD after all tests does not fail closed if checkout provenance is wrong.

Disabling persisted checkout credentials keeps the read-only job from leaving an unnecessary repository token in git configuration for later validation steps. The explicit pre-test identity check makes exact-source provenance a prerequisite for producing any promotion-policy validation evidence. This changes no policy threshold, promotion decision, model, champion pointer, calibration input, or evidence payload.

## Proof sequencing

`.github/workflows/promotion-policy-workflow-provenance-proof.yml` is intentionally manual-only. It verifies both the dispatched SHA and the live remote branch head before running the focused contract and adjacent promotion regressions.

Do not dispatch this proof while the canonical evaluation sequence `#100 → #436` owns the shared HaxLab runner. A later green proof is narrow provenance evidence only; it does not authorize Arena merge or champion promotion.
