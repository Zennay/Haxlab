# Promotion v2 frozen evaluation suite

## Purpose

This is the pre-Candidate-E promotion suite. It is frozen before any Candidate E
training or model change and must not be replaced after challenger results are
seen.

The suite is shared by the replay-seeded champion duel and Closed-Loop
Evaluation Arena v2.

## Why this is v2

An earlier promotion-v2-frozen-suite-v1 artifact was found to be unsuitable as
fresh promotion evidence. Its source 01 replay

977c70b0184e3acd677a6cde36137030a03fc1228d87fb14f99a4491b971a8ad

had already been evaluated with Candidate B in
champion-arena-v2-runs/candidate-b/source-04-* before v1 was frozen. The v1
artifact is preserved as audit evidence but is superseded and must not be used
as Candidate E unseen promotion evidence.

## Frozen artifact

VPS root:

/var/lib/haxlab/derived/evaluation/promotion-v2-frozen-suite-v2

Repository descriptor:

configs/evaluation/promotion-v2-frozen-suite-v2.json

Frozen manifest SHA-256:

fc255d642995638c4d1cc23a709da660c6c0ed3c43ed77a20386f4134415d315

The preselection exclusion set contains 1,148 replay SHA-256 values. It includes
the Candidate B train/validation/frozen-holdout population plus all consumed
Candidate B/C/D evaluation sources, including the contaminated 977c... source
above.

Preselection exclusion SHA-256:

1a8c6e50549ef6a8bfa90e23800bb6d7636286b6843b25c94a45fd44fc551916

The final audit exclusion file also includes the three selected source replays,
for 1,151 SHA-256 values total:

14585a289d8cf8064e4ad6b90151a7984e61481135036cf82602a712baaad46b

## Frozen sources

Each source contains 16 deterministic replay-seeded scenarios, history window
8, sample cadence 6 ticks, and mirrored red/blue evaluation for 32 replay-duel
matches per source.

1. 95dd4f336c0936c814438d41e73cc52f2d92085e9376817ccb39bfc4bb21398d
   - scenarios SHA-256:
     6de46cf9f325998a6415af7831403335bb11b25a35a247bf9ec74c943d2f9ceb
2. 909f8f9f23905a91ae5de4cebb5384dafeaab2347239858b3d75ec428fd5e8b1
   - scenarios SHA-256:
     b63bd41e1cc832d3c41cd889b084e03b3e4452ebce7632826183b806875d7be1
3. f1786f471b6c81f59e707bac36f7e9a4b2ad5c3ce157254a5161221a2f4d7fba
   - scenarios SHA-256:
     5569d213f5f98e4bdb287b68294dbf2e266e353039bc293b372408e8f77f50dd

Total planned mirrored replay-duel matches: **96**.

The descriptor records SHA-256 values for every source.json, stadium.hbs, and
scenarios.json file. A selected source may not occur in the preselection
exclusion set, and all three selected replay hashes are distinct.

## Validation

Static integrity validation passed before this descriptor was committed:

- manifest schema/counts are consistent;
- descriptor and exclusion hashes match the frozen files;
- all required consumed Candidate B/C/D evaluation hashes are in the
  preselection exclusion set;
- no selected source occurs in that preselection set;
- all per-source file hashes match;
- all sources contain exactly 16 scenarios with history window 8 and sampling
  every 6 ticks.

### Arena v2 identity control

All three sources were run with the current production champion as both
challenger and reference using the exact frozen Arena-v2 calibration horizon:

- 16 scenarios/source;
- 30 seconds;
- both sides;
- full-team and GK/DM/AM/ST plug-and-play tracks;
- raw-policy-only;
- calibrated thresholds enabled.

Every source returned:

- structurally_valid = true;
- behavior_gate_passed = true;
- eligible_for_live_promotion = true;
- reason closed_loop_arena_v2_passed.

A shorter 5-second smoke is intentionally not a valid absolute-threshold
identity test because the Arena-v2 absolute context-adaptation thresholds were
calibrated at 30 seconds.

### Replay-duel consumer smoke

The replay benchmark + replay duel + multisource aggregator consumed all three
sources successfully. A two-scenario-per-source identity smoke produced 12
mirrored matches, three distinct scenario hashes, and aggregate identity match
score 0.5.

This smoke proves format/consumer compatibility only; Candidate E promotion
must use the full frozen suite and unchanged promotion policy.

## Freeze rule

Do not swap, regenerate, or cherry-pick any of these three sources after
Candidate E results exist. Any future suite version requires a new predeclared
gate, a new exclusion set covering all newly consumed evidence, and a new
immutable manifest.

Production current.json / live.json champion pointers were not changed by
freezing or validating this suite.
