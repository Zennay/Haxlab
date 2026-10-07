# Runtime-model workflow provenance contract

The runtime-model integrity workflow supports both branch/manual validation and pull-request validation, then reads current HaxLab runtime model artifacts. Its expected source SHA is therefore event-dependent and must be proven before any evidence-producing step runs.

## Invariants

`.github/workflows/runtime-model-integrity-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. choose `pull_request.head.sha || github.sha` as the immutable source for both checkout and immediate verification;
4. use `clean: true` and `persist-credentials: false`;
5. fail closed unless `git rev-parse HEAD` equals that expected source before Node syntax, regressions or live runtime-model reads;
6. preserve the final exact-head evidence assertion as a second boundary.

The workflow's own path is included in its PR trigger. Therefore this staging branch intentionally stays PR-less until the shared HaxLab runner can accept the resulting validation job.

This lane changes no runtime implementation, model artifact, test semantics, threshold, champion pointer, promotion state or Arena behavior.

## Proof sequencing

`.github/workflows/runtime-model-workflow-provenance-proof.yml` is source-only and manual. It validates exact dispatched/live branch identity plus this workflow contract without reading current runtime artifacts.

Keep it undispatched and do not open the candidate PR while canonical `#100 → #436` sequencing remains owner-held.
