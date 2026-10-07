# BC baseline bundle post-publication audit

HaxLab treats a trained behavioral-cloning baseline as a paired artifact: `model.npz` contains the NumPy parameters and `metrics.json` contains the architecture, training and holdout evidence needed to interpret those parameters. Producer completion alone is not sufficient evidence that those bytes remain safe to consume later.

Run the independent verifier against a finalized baseline output directory:

```bash
python -m haxlab.learning.baseline_audit /var/lib/haxlab/derived/training/baseline/current
```

A clean audit exits 0 and emits schema `haxlab-bc-baseline-audit-v1`. Contract failures exit 2 with a compact machine-readable error on stderr.

## Fail-closed contract

The verifier independently checks:

- the bundle directory, `metrics.json` and `model.npz` are non-symlink regular objects; metrics/model hard-link aliasing is rejected;
- the bundle root is pinned to one `O_DIRECTORY|O_NOFOLLOW` descriptor, both files are opened relative to that descriptor, and the logical bundle path must still resolve to the same directory identity before success;
- both files are read through bounded no-follow descriptors, must remain byte/identity stable during the read, and each logical member path must still resolve to the descriptor-bound inode after reading;
- metrics JSON is valid UTF-8/JSON with no duplicate object keys or non-finite JSON constants;
- the NPZ is a bounded ZIP with exactly the eight baseline arrays and no duplicate/encrypted/unsupported members;
- all parameters are float32, finite and architecture-shape compatible;
- normalization standard deviations are strictly positive;
- schema, canonical input/excluded columns, 9-way direction mapping and architecture metadata are exact;
- training counts and rates are native, finite and internally coherent;
- history length/epoch order matches the declared epoch count;
- holdout confusion counts sum to samples and derived precision/recall/F1/rates agree with those counts;
- the final holdout kick threshold is bound to the published calibrated threshold;
- the metadata model path names `model.npz` and train/holdout index paths are distinct.

The success receipt binds the exact metrics/model byte SHA-256 values and a semantic inventory SHA-256 over model tensor contents, shapes/dtypes and the validated model/training metadata.

## Scope boundary

This auditor never trains, repairs or republishes a model. It deliberately does not edit `haxlab.learning.baseline`, shard/manifest producers or auditors, live/champion inference, runtime state, evaluation code or champion pointers. Producer-side baseline input provenance remains owned by its existing lane.
