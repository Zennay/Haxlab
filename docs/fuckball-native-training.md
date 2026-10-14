# HaxLab training in the native FuckBall engine

The HaxLab workflow [fuckball-native-selfplay.yml](../.github/workflows/fuckball-native-selfplay.yml) runs actual headless **4v4** matches in the independent node-haxball 2.3.1 engine used by FuckBall. This replaces synthetic kinematics with the same stadium collision, player acceleration, kick and game-state logic used by the game.

## Execution

- Scheduled nightly at 02:17 UTC after the workflow reaches the default branch, and manually from **Actions → HaxLab / FuckBall native-engine 4v4 training → Run workflow**.
- Runs on a dedicated GitHub-hosted runner, **not** the serialized VPS HaxLab evaluation/ingestion runner. No writes to `/opt/haxlab`, the replay archive, the model registry or the live champion pointer.
- Fetches an immutable FuckBall source revision `fac87e72b42c83a20f8e86ae01e000aeaec2faeb`, checks exact checkout HEAD and the original BFF Big v4 + physics-engine SHA256 fingerprints, and runs the upstream `npm test` suite.
- Calls upstream `training/haxlab_selfplay.cjs` (48 episodes and six tactical generations by default). Every episode has eight bots. Two stadium variants, **BFF Big v4** and **Classic**, are used. Side and seed variation are applied.
- Each scheduled run derives a distinct 32-bit seed from its GitHub run ID (or a validated explicitly supplied manual seed), records it in the receipt, and varies episode setup and tactical evolution while preserving fixed 60 Hz physics. The same seed reproduces the same simulation on an identical source revision.\n- Records decisions every four simulation frames (previously every twelve) to capture more rare kick/contact states without touching gameplay physics. Records HaxLab-compatible compressed float32 shards, trains a neural behavioral-cloning/DAgger candidate using the original HaxLab trainer ported in FuckBall, and runs a four-match side-swapped arena benchmark.
- Split membership is by whole match: four training matches, one calibration/validation match and one held-out test match per six episodes. No game or shard is shared across splits; the threshold is selected on validation, not the independent test.
- The HaxLab `tools/fuckball_native_training_receipt.py` verifier rejects truncated, nonfinite or overlapping episodes, incorrect stadium labels, mismatched shards, missing independent test evidence and incomplete or duplicate arena sides.

## Outputs and safety

The Actions artifact `fuckball-native-haxlab-<run id>` retains 14 days of:
- `candidate.json` neural weights, `weights/model.npz` and `weights/metrics.json`;
- `team-policy.json` evolved team behavior;
- per-match shards, metadata and three split indexes;
- `arena.json`, with the neural candidate playing against tactical bots on both sides of both maps;
- `receipt.json` with source/engine identifiers, episode counts, row counts, metrics and artifact hashes.

Nightly batches use different seeds, so they explore more match openings instead of reproducing the identical dataset each night. This is independent repeated training, not continual fine-tuning of the previous candidate; each run still starts from the pinned baseline until a separately authorized versioned training-source registry is available.\n\nAll candidates are **evaluation only**. A win, increased territorial pressure or higher offline accuracy in this small batch is not evidence of elite skill; the HaxLab Arena v2 frozen scenarios, champion/older champion comparisons, guard-independence and controlled live canary remain mandatory before any promotion.

This initial job starts each independent nightly candidate from the current upstream bundled baseline model. **It does not yet continuously chain a prior night's challenger into the next night's source model, and it does not train on live HaxLab replay data.** Those require a versioned, formally gated candidate registry and replay-to-rollout adapter. Treat this as the safe native-engine training bridge rather than full autonomous promotion.
