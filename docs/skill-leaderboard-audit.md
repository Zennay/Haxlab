# Skill leaderboard audit contract

`haxlab.skill.leaderboard_audit` is a read-only verification boundary for an already-published `haxlab-skill-leaderboard-v1` snapshot.

It is deliberately separate from the leaderboard producer. The producer owns scoring and publication; the auditor answers a narrower question: **are these exact published bytes still structurally and semantically safe to consume?**

## Safety boundary

The auditor:

- opens the final leaderboard with no-follow and non-blocking file semantics;
- requires a regular file and rejects oversized input;
- reads the same descriptor twice and rejects byte or file-identity drift during the audit;
- rechecks the final logical pathname after the descriptor read and rejects path replacement, redirection, or disappearance before success;
- rejects invalid UTF-8, invalid JSON, duplicate object keys and non-finite JSON constants;
- requires the exact v1 top-level contract, while allowing the active producer follow-up's optional `analysis_version`;
- validates native threshold types and row compatibility with `min_matches` / `min_minutes`;
- requires unique canonical `player_id` values and the producer's canonical rating ordering;
- validates finite rating/context values plus the exact eight-dimension surface;
- checks that `rating` is consistent with the published `overall_z` transformation.

The module never rewrites, repairs or normalizes leaderboard bytes.

## Receipt

A successful audit prints compact JSON with schema `haxlab-skill-leaderboard-audit-v1` and:

- `ok: true`;
- exact source `size_bytes` and `sha256`;
- `row_count`;
- optional `analysis_version`;
- `inventory_sha256`, a deterministic digest over the semantic source/version/threshold/row inventory.

The byte digest intentionally changes when formatting or `generated_at` changes. The inventory digest excludes `generated_at` so equivalent semantic leaderboard contents have a stable comparison key.

## CLI

```bash
python -m haxlab.skill.leaderboard_audit /var/lib/haxlab/derived/skill/leaderboard.json
```

Success exits 0 and writes the receipt to stdout. Contract failures exit 2 and write a compact machine-readable failure object to stderr.

## Scope separation

This verifier does not modify `skill/leaderboard.py`, the training selector, shard generation, runtime state/finalization, ingestion, evaluation or champion state. Producer-side analysis-version publication remains owned by GitHub issue #131 / PR #132.
