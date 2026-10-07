# Scenario-source workflow provenance contract

The scenario-source validator combines repository regressions with read-only verification of current frozen replay bytes on the HaxLab VPS. Repository source identity must therefore be immutable and proven before either class of evidence executes.

## Invariants

`.github/workflows/scenario-source-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before environment setup, pytest, or any VPS replay/database read;
5. preserve read-only permissions, runner/timeout/triggers, existing scenario-source + multisource regressions, and the frozen replay-byte verification step.

This changes no scenario-source implementation, replay database/archive bytes, frozen manifest, multisource semantics, threshold, model, champion pointer, or promotion state.

## Proof sequencing

`.github/workflows/scenario-source-workflow-provenance-proof.yml` is intentionally source-only and manual. It verifies exact dispatched/live branch identity and the workflow contract without reading `/var/lib/haxlab` or using the haxlab service account.

Keep it undispatched while canonical `#100 → #436` sequencing remains owner-held. The canonical scenario-source workflow remains the authority for live frozen-byte evidence after integration.
