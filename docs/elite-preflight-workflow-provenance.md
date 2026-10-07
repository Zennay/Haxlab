# Elite preflight workflow provenance contract

The elite preflight validator produces exact-head evaluation evidence before promotion decisions are considered. Its checkout implementation and checked-out repository head must therefore be immutable and verified before the test environment or gate regressions run.

## Invariants

`.github/workflows/elite-gate-preflight-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before environment setup;
5. preserve read-only permissions, `[self-hosted, haxlab]`, timeout, triggers, and the existing elite/promotion/closed-loop regression surface.

The change is provenance-only. It does not change elite evidence semantics, numeric thresholds, policy behavior, models, champion pointers, promotion state, or frozen inputs.

## Proof sequencing

`.github/workflows/elite-preflight-workflow-provenance-proof.yml` is manual-only and validates this source contract on an exact dispatched/live branch head plus the adjacent regressions.

Keep it undispatched while canonical `#100 → #436` sequencing remains owner-held. A later green proof is narrow workflow-provenance evidence only.
