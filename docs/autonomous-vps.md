# Autonomous VPS Runtime

HaxLab is designed to stay alive on a VPS while replay data arrives in batches.

The VPS should not retrain continuously just because a new file exists. It should make evidence-driven decisions about when enough useful new data exists to justify a new dataset/model cycle.

## Upload contract

Recommended staging directory:

```text
/var/lib/haxlab/incoming/
```

Upload files with a temporary suffix and rename them atomically when complete:

```bash
scp replay.hbr2 user@vps:/var/lib/haxlab/incoming/replay.hbr2.part
ssh user@vps 'mv /var/lib/haxlab/incoming/replay.hbr2.part /var/lib/haxlab/incoming/replay.hbr2'
```

HaxLab only scans final `.hbr2` files.

For a large first batch, `rsync`/SFTP is preferable. Future batches can be added to the same directory or subdirectories.

## Runtime directories

```text
/var/lib/haxlab/
├── incoming/                 # upload staging
├── raw/
│   └── replays/             # immutable content-addressed archive
├── derived/                  # parser/features/datasets
├── models/
│   ├── challengers/
│   └── champions/
├── state/
│   └── haxlab.sqlite3       # durable runtime ledger
└── logs/
```

The raw archive is content-addressed by SHA-256. Multiple filenames containing the same replay become one raw object plus multiple provenance records.

## Continuous loop

```text
upload
  ↓
settled-file scan
  ↓
hash + dedupe
  ↓
immutable raw archive
  ↓
parse queue
  ↓
features
  ↓
quality assessment
  ↓
skill/context refresh
  ↓
dataset delta
  ↓
train trigger? ── no ──> idle
  │
 yes
  ↓
challenger training
  ↓
evaluation arena
  ↓
promote/reject
  ↓
failure mining
  ↓
curriculum update
  ↓
idle / wait for new data
```

## Important autonomy rule

A new upload does **not** automatically replace the model.

Training can be triggered by policy, for example:

- at least N new usable matches;
- at least N new Gold/Elite matches;
- enough new high-confidence player observations;
- a scheduled research cycle if the dataset changed;
- a manually requested experiment.

Promotion remains separately gated by the evaluation arena.

## First ~6000 replays

The initial import should be treated as a bootstrap dataset:

1. archive/dedupe all files;
2. validate HBR2 headers/deflate payloads;
3. parse a canary sample;
4. measure parser success/failure distribution;
5. parse the full batch;
6. calculate match quality;
7. build player identities and contextual skill estimates;
8. freeze a holdout set **before** training;
9. create the first human-imitation dataset;
10. establish baseline agents;
11. train challenger 1;
12. evaluate and store all evidence.

Do not call the highest-scoring people "elite" until the sample size and identity confidence are sufficient.

## Later uploads

New replay batches are incremental:

- existing content hashes are skipped;
- new raw objects are appended;
- only affected aggregates are recomputed where possible;
- dataset manifests receive new versions;
- the frozen evaluation set is not silently contaminated with newly trained examples;
- old champions remain available for regression testing.

## Resource policy

The daemon should stay lightweight when idle.

CPU-heavy parsing/training runs are explicit jobs with concurrency limits. Training and evaluation must not starve ingestion or make the VPS unresponsive.

The runtime ledger is the source of truth for what has already been seen, archived, parsed, trained on and evaluated.
