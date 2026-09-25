# Self-Improving Learning Loop

## Objective

Build agents that improve through measured iteration without blindly copying weak human games or accepting regressions.

## Loop

1. ingest new replay data;
2. validate and score data quality;
3. update contextual player/skill estimates;
4. build a versioned curriculum;
5. train a challenger;
6. evaluate the challenger;
7. promote or reject;
8. mine failures;
9. add hard cases to future curricula;
10. repeat.

## Human-data curriculum

Training examples should be weighted by:

- match quality;
- confidence in player identity;
- player skill confidence;
- state difficulty;
- action/outcome quality;
- role relevance.

Gold/Elite human examples can seed behavioral cloning. Lower-quality data can still help representation learning or analytics but should not have equal imitation weight.

## Self-play population

Do not train only against the latest copy of the agent.

Maintain a population including:

- current champion;
- previous champions;
- scripted baselines;
- exploiters/specialists;
- style-diverse agents.

This reduces overfitting to a single opponent.

## Failure mining

Evaluation failures should become structured artifacts.

Examples:

- kickoff failures;
- wall traps;
- last-man defence;
- counters;
- finishing;
- score/time management;
- rare ball trajectories.

High-impact failures become regression scenarios and/or training curriculum items.

## Promotion invariant

A newly trained model is only a challenger.

It becomes champion only when it passes predefined evaluation gates.

**Newer does not mean better.**


## Behavioral-cloning bootstrap

Human imitation is a bootstrap stage, not the final agent.

Current pipeline:

```text
state-pass-v4
→ conservative player selection
→ deterministic train / frozen holdout replay split
→ Float32 replay shards
→ fit / internal validation split
→ behavior-cloning challenger
→ frozen holdout evaluation
→ live Node policy runtime
→ arena / scenario evaluation
→ self-play
```

### Leakage rules

The frozen replay holdout is not used to:

- normalize features;
- choose epochs;
- choose kick thresholds;
- select hyperparameters;
- select player identities.

A deterministic validation subset is carved only from the training split.
Hyperparameter and kick-threshold decisions happen there. The frozen holdout is
evaluated only after those decisions are fixed.

### Inputs and outputs

The first BC policy deliberately excludes replay time, local player index and
team ID from model inputs. It learns from canonicalized physical state:

- own position and velocity;
- relative ball position and velocity;
- nearest teammate states;
- nearest opponent states;
- presence masks.

The policy predicts:

- one of nine movement directions;
- a separate kick probability.

A saved kick threshold converts kick probability into the live binary action.

### Offline metrics

Do not judge a policy only by overall action accuracy. Retain:

- direction accuracy;
- nine-class direction confusion matrix;
- macro direction recall;
- kick precision / recall / F1;
- predicted and true kick rate;
- joint direction+kick accuracy;
- majority-direction baseline.

Replay quality weights may affect training loss. Holdout metrics remain
unweighted so evaluation is not made artificially favorable.

### Live policy artifact

A challenger exports both:

- `model.npz` for Python analysis;
- `policy.json` for a dependency-light Node runtime.

The Node runtime reproduces feature normalization, the MLP forward pass,
direction argmax and kick threshold. Exporting a runnable policy does not make
it a champion; promotion still requires the arena gates below.

### Full materialization

Full train/holdout shard extraction runs as a resumable VPS service rather than
holding a GitHub Actions runner for the entire build. Every replay is cached,
and progress checkpoints are written periodically. Deploys may pause and resume
the service safely.
