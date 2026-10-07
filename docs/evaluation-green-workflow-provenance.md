# Evaluation green integration workflow provenance

The evaluation-green integration workflow combines repository-wide Python/Node regressions with current runtime-model and frozen scenario-state checks on the HaxLab VPS. Its repository source must be immutable and verified before any of those evidence layers execute.

## Invariants

`.github/workflows/evaluation-green-integration-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before environment setup, repository tests, runtime-model reads, or frozen-state reads;
5. preserve read-only permissions, `[self-hosted, haxlab]`, 45-minute timeout, trigger, full Python suite, integrated Node regressions, live runtime checks and frozen scenario validation.

This lane changes no evaluation implementation, runtime model, frozen scenario byte, threshold, champion pointer, promotion state, or integration decision. It strengthens only the source provenance prerequisite.

## Proof sequencing

`.github/workflows/evaluation-green-workflow-provenance-proof.yml` is source-only and manual. It proves exact dispatched/live branch identity plus this workflow contract without reading HaxLab live-state paths.

Keep it undispatched while canonical `#100 → #436` sequencing remains owner-held. The hardened integration workflow itself remains the authority for live runtime/frozen-state evidence after integration.
