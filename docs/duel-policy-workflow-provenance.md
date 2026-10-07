# Duel-policy workflow provenance contract

The duel-policy integrity workflow is evaluation evidence, so its own execution provenance must be reproducible before its test results are trusted.

The workflow pins `actions/checkout` to the reviewed immutable v4 commit `11d5960a326750d5838078e36cf38b85af677262`, disables persisted checkout credentials, and checks out an immutable source SHA. On pull requests that source is the real `pull_request.head.sha` rather than GitHub's synthetic merge commit; on push/manual runs it falls back to `github.sha`.

Immediately after checkout the workflow fails closed unless `git rev-parse HEAD` equals that selected source SHA. The focused regression contract runs before the duel-policy and adjacent evaluation suites and protects the immutable checkout ref, PR-head/event-SHA selection, explicit post-checkout identity assertion, and the provenance-hardening branch trigger.

This lane changes validation provenance only. It does not change duel thresholds, duel evidence semantics, multisource decisions, model files, champion pointers, promotion state, or the canonical Arena integration workflow. A green workflow run is narrow evidence for this workflow head and does not authorize Arena merge or champion promotion.
