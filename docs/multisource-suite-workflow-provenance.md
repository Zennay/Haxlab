# Multisource-suite workflow provenance contract

The multisource-suite integrity workflow produces evaluation evidence, so its checkout and exact-head claim must be bound to one immutable source revision.

The workflow pins `actions/checkout` to reviewed immutable v4 commit `11d5960a326750d5838078e36cf38b85af677262`, disables persisted checkout credentials, and selects an immutable source SHA. Pull-request runs use the real `pull_request.head.sha` instead of GitHub's synthetic merge commit; push and manual runs use `github.sha`.

Immediately after checkout the workflow fails closed unless `git rev-parse HEAD` equals the selected source SHA. The focused provenance contract runs before the existing multisource-suite, scenario-source, and multisource-duel regressions.

This lane changes validation provenance only. It does not change frozen-suite configuration, suite generation semantics, scenario-source selection, multisource-duel decisions, thresholds, models, champion pointers, promotion state, or the canonical Arena integration workflow. A green run is narrow evidence for this workflow head and is not merge or promotion authorization.
