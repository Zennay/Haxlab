# Evaluation Arena

## Purpose

Prevent apparent progress from replacing real progress.

## Test categories

A challenger should be tested against:

- previous champion;
- multiple older champions;
- fixed scripted baselines;
- population opponents;
- frozen scenario suites;
- human holdout situations never used for training.

## Control variables

Where applicable, rotate:

- team side;
- start position;
- random seed;
- role;
- score state;
- time remaining;
- opponent style.

## Metrics

At minimum retain:

- win/loss/draw;
- goal differential;
- scoring and conceding rates;
- possession/territory;
- turnover rate;
- scenario success;
- stability/error rate.

Use confidence intervals rather than one small batch of games.

## Regression gate

Promotion must fail if a challenger shows a severe regression in an important frozen scenario even when aggregate win rate rises.

## Reproducibility

Every evaluation record stores:

- challenger artifact/version;
- opponent versions;
- code commit;
- physics/environment version;
- config;
- seeds;
- dataset IDs;
- results.

## Champion promotion

Promotion policy should eventually be machine-readable, but human-readable evidence always remains available.

The first implementation should be deliberately conservative.
