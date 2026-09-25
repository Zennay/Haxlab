# Behavior Cloning Experiment Plan

## Purpose

Turn the first HaxLab imitation model into a reproducible baseline rather than
immediately optimizing against one lucky run.

The production reference remains the current-state, unweighted NumPy MLP pilot
on the frozen state-pass-v4 holdout. Experimental changes live off `main` until
their effect is measured.

## Reference experiment

Keep these fixed for the first reference:

- training split: existing deterministic state-pass-v4 train set;
- holdout split: existing deterministic frozen holdout;
- pilot: 200 train replays / 50 holdout replays;
- sample interval: every 6 ticks;
- hidden dimension: 64;
- epochs: 4;
- batch size: 8192;
- learning rate: 0.001;
- L2: 0.00001;
- seed: 1337.

Record at least:

- train and holdout replay/sample counts;
- direction accuracy;
- majority-direction accuracy and lift;
- kick precision / recall / F1;
- true and predicted kick rate;
- joint action accuracy;
- storage and runtime.

## Diagnostics before optimization

A single aggregate accuracy is insufficient.

Add:

- 9×9 direction confusion matrix;
- per-direction recall;
- macro direction recall;
- kick calibration (`predicted_rate` vs `true_rate`);
- model / train-index / holdout-index SHA-256 fingerprints.

## Experiment A — lagged state

Question: how much of current-state direction accuracy comes from observing
velocity that may already reflect the current input?

Control:

- same replay split;
- same selected players;
- same model/training hyperparameters;
- predict action at tick `t` from reconstructed state at tick `t-1`.

Compare to the reference on the same frozen holdout.

## Experiment B — skill-weighted human imitation

The manifest already stores a bounded replay `example_weight` derived from the
selected player's conservative skill estimate. Production baseline currently
does not use it.

Control:

- same shards and model;
- apply replay example weights only to training loss;
- keep holdout metrics completely unweighted.

This tests whether stronger-human examples actually improve generalization
instead of merely changing the evaluation distribution.

## Experiment C — strict split before player selection

Current state-pass-v4 replay train/holdout membership is deterministic and has
no replay overlap, but the elite-player leaderboard is built before the replay
split. This means holdout performance can influence which players enter the
imitation curriculum.

Strict dataset mode:

1. quality-filter replay candidates;
2. freeze train/holdout membership by replay SHA;
3. build skill/player selection from train replay IDs only;
4. use the resulting player identities for both train extraction and blind
   holdout evaluation.

This should become the default for the next dataset revision if its coverage is
sufficient.

## Experiment discipline

Change one major variable at a time.

For promising configurations:

1. rerun with multiple deterministic seeds;
2. report mean and spread, not just the best seed;
3. retain the original frozen holdout;
4. do not promote a supervised model to champion from imitation metrics alone.

The BC model is a challenger artifact. Champion promotion requires a playable
agent and the existing arena / frozen-scenario / regression gates.
