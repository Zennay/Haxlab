# BC baseline shard admission — reject corrupt evidence

The behavior-cloning baseline trains from float32 imitation shards. Corrupt shard rows previously could be normalized, rounded into action labels, and used to fit or evaluate a model without explicit rejection.

The actual BC baseline consumer now enforces:

1. `rowWidth` must be a native positive integer. `columns` must be a non-empty list of unique non-empty native string names, and its length must match the width exactly.
2. Decompressed float32 payload must have a full number of rows and **all values must be finite**. NaN and Infinity are rejected before normalization or model computation.
3. `dir_x` and `dir_y` must be exactly `-1`, `0` or `1`; `kick` must be exactly `0` or `1`. The loader no longer rounds, clips or thresholds corrupt action targets.
4. Every training and holdout shard must present the same **ordered feature names** used to calculate normalization and the model input. A reordered but equal-width shard fails instead of being silently misinterpreted.

The code changes only `src/haxlab/learning/baseline.py` and preserves the previous valid training path. Original product source from historical PR-less branch `feature/data-pipeline-baseline-shard-integrity-20261007` is integrated on current main; the old branch remains untouched and its tests/proof workflows are not carried over.

This is a **source-only** safeguard. No active training, model, held-out dataset, player/game runtime, queue or champion state was mutated. Existing exact-head automatic CI, independent review and release gates must approve production integration; no QA suites have been added or manually run.
