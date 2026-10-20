# Canonical skill identity model admission — #415

The genuine source model constructors now reject ambiguous player and role keys **before** estimates, training artifacts or leaderboard outputs can be grouped by misleading identities. This ports only the production-source correction from the historical [issue #415 candidate](https://github.com/Zennay/Haxlab/issues/415); original staging and QA branch sources remain untouched.

`SkillObservation.player_id`, optional `SkillObservation.role`, and `PlayerSkillEstimate.player_id` must be **native non-empty strings with no leading or trailing whitespace**. Whitespace-only values, coercible objects/bytes and subclasses raise stable `ValueError` diagnostics. Existing already canonical identities such as `auth:abc123`, `player-a`, and `midfield` are unchanged. This is validation, **not normalization**: silently trimming before storing would lose fidelity and could merge two distinct upstream identities.

The change does not alter performance dimensions, context adjustment, ranking, skills math, identities in other services, WoningNet, runtime state, model/champion promotion or source data. No QA files, workflows, regressions or manual suites have been added/invoked. Required exact-head CI, independent review and source-owner release approval remain prerequisites for merge and any actual production use.
