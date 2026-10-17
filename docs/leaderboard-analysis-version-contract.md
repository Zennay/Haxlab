# Leaderboard analysis-version contract

The skill leaderboard is the producer immediately upstream of the human-imitation
training manifest. The manifest builder binds its provenance to the analysis
version carried by that leaderboard.

## Producer contract

`haxlab-skill --root <analysis-root>` publishes
`haxlab-skill-leaderboard-v1` with:

- `analysis_version`: the final directory name of `--root`;
- `source_root`: the full analysis-root path;
- the existing threshold fields and leaderboard rows.

An analysis root without a non-empty final directory component is invalid rather
than producing ambiguous version evidence.

## Consumer contract

`build_training_manifest()` requires a native non-empty
`leaderboard.analysis_version` and copies that value to the resulting
`haxlab-human-imitation-manifest-v3`.

This makes the normal VPS chain deterministic:

```text
derived/<analysis_version>
        ↓
haxlab-skill
        ↓
leaderboards/<analysis_version>.json
        ↓
haxlab-training-manifest
        ↓
human-imitation-<analysis_version>.json
```

A producer regression that removes the version field must fail before deployment,
because the focused regression exercises the real leaderboard JSON output directly
through the manifest builder.
