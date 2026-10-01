# HaxLab Player v0.1 — Elite 4v4 Imitation Agent

HaxLab Player v0.1 is the first pipeline aimed at producing a bot that can
actually play fixed 4v4 HaxBall.

The target formation is always:

```
GK -> DM -> AM -> ST
```

## What changed from the old BC baseline

The old baseline remains available as a benchmark. Player v0.1 adds:

- elite-only training examples selected with conservative skill estimates;
- explicit GK/DM/AM/ST role conditioning;
- full 4v4 state: all 3 teammates and all 4 opponents;
- canonical attack direction so red and blue share one coordinate system;
- score difference;
- skill weighting so stronger human evidence counts more;
- role balancing so one position cannot dominate the dataset;
- temporal windows instead of isolated one-frame decisions;
- separate movement and kick heads;
- validation-only model selection and kick-threshold calibration;
- a frozen holdout that is not used for architecture or threshold selection;
- a live JSONL inference runtime;
- a node-haxball plugin capable of controlling four fake players.

## Dataset pipeline

```
state-pass-v4 analyses
        |
        v
4v4 role inference + leaderboard
        |
        v
elite selection per role
        |
        +--> train
        +--> validation
        +--> frozen holdout
        |
        v
full-state shards
        |
        v
causal temporal windows
        |
        v
role-conditioned multitask policy
        |
        +--> 9-way movement head
        +--> kick probability head
```

Known aliases are stored in `configs/player_aliases.json`. The first known
pairs are:

- `misio -> sekai`
- `sw1zy -> swizy`

The alias layer is only used to canonicalize training identity. It does not
naively average leaderboard ratings.

## One-command VPS pilot

First produce a current 4v4 leaderboard:

```bash
sudo -u haxlab env PYTHONPATH=/opt/haxlab/src \
  /opt/haxlab/.venv/bin/python -m haxlab.skill.leaderboard \
  --root /var/lib/haxlab/derived/state-pass-v4 \
  --top 10000 --min-matches 1 --min-minutes 0 \
  --format json \
  --output /var/lib/haxlab/derived/leaderboards/state-pass-v4.json \
  >/tmp/haxlab-leaderboard.json
```

Then run the pipeline:

```bash
sudo -u haxlab env PYTHONPATH=/opt/haxlab/src \
  /opt/haxlab/.venv/bin/python -m haxlab.learning.elite_pipeline \
  --analysis-root /var/lib/haxlab/derived/state-pass-v4 \
  --leaderboard /var/lib/haxlab/derived/leaderboards/state-pass-v4.json \
  --raw-root /var/lib/haxlab/raw/replays \
  --work-root /var/lib/haxlab/derived/training/elite-player-v01 \
  --node-script /opt/haxlab/tools/extract_elite_imitation.js \
  --aliases /opt/haxlab/configs/player_aliases.json \
  --window 8 \
  --epochs 5
```

For a smaller first pilot, add for example:

```bash
--replay-limit 200 --epochs 3
```

## Evaluation rules

The training split fits weights.

The validation split chooses:

- best epoch;
- kick decision threshold.

The frozen holdout is evaluated only after both choices have been finalized.
Do not tune on `final_holdout`.

Important metrics:

- direction accuracy vs majority-direction baseline;
- joint direction+kick accuracy;
- kick precision / recall / F1;
- predicted kick rate vs true kick rate;
- the same metrics per GK/DM/AM/ST.

## Runtime

Start the policy runtime:

```bash
haxlab-elite-agent \
  --model-dir /var/lib/haxlab/derived/training/elite-player-v01/model \
  --stdio
```

It accepts JSON lines:

```json
{"command":"act","request_id":1,"agent_id":"dm-1","role":"dm","features":{"own_x":0}}
```

A real request must contain every feature named in the model metadata.

The runtime keeps a temporal history per `agent_id`, so four players can share
one model process while each keeps its own state window.

## Live bot plugin

`tools/elite_bot_plugin.js` is a node-haxball CreateRoom plugin.

It can spawn four in-memory players:

- HaxLab-GK
- HaxLab-DM
- HaxLab-AM
- HaxLab-ST

It canonicalizes live game state using the same feature layout as the training
extractor, sends a state approximately every six ticks to the Python policy and
applies returned actions with `fakeSendPlayerInput`.

Environment variables:

```bash
HAXLAB_ELITE_MODEL_DIR=/var/lib/haxlab/derived/training/elite-player-v01/model
HAXLAB_ELITE_PYTHON=/opt/haxlab/.venv/bin/python
HAXLAB_ELITE_AUTOSPAWN=1
```

The plugin is intentionally not auto-enabled in production yet. The first live
match should happen only after the pilot model passes offline evaluation.

## v0.1 promotion gate

Do not call a model a champion merely because training completed.

A v0.1 candidate should at minimum:

1. beat the existing BC baseline on frozen direction lift;
2. avoid pathological kick spam;
3. produce sensible per-role behavior on replay-state inspection;
4. survive a live room smoke test without input/runtime instability;
5. then play benchmark matches against deterministic chase/defensive bots.

Self-play/RL belongs after this gate, not before it.


## Champion candidate run

The first serious v0.1 candidate is intentionally larger than the PR smoke:

- up to 800 training replays;
- up to 200 validation replays;
- the complete frozen holdout split;
- temporal window 8;
- hidden layers 128 and 96;
- 3 epochs;
- 12 local BFF Big v4 sandbox matches, with Red/Blue side swaps;
- balanced, compact and pressing scripted opponents;
- physical kick-range gating at 31 px;
- side/role telemetry to detect mirrored-runtime regressions.

The candidate is not promoted from offline accuracy alone. Frozen-holdout integrity,
runtime parity and sandbox behavior are all preserved as separate evidence.
