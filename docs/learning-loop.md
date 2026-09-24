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
