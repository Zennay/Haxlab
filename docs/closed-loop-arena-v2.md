# Closed-Loop Evaluation Arena v2

## Goal

Arena v2 answers the question that offline imitation accuracy cannot:

> Does this policy keep playing intelligently after its own actions change the next state?

The arena has two equally required evaluation tracks.

### 1. Squad intelligence

All four challenger roles (GK / DM / AM / ST) play together. The arena measures:

- closed-loop match/proxy performance;
- progression and territory;
- team formation order;
- collapse / overstretch of team shape;
- role deviation;
- stalls, boundary drift and OOD;
- context-sensitive action changes.

### 2. Plug-and-play individual intelligence

One challenger role is inserted into a team whose other three roles come from a
partner-policy population. This is repeated for GK, DM, AM and ST, mirrored on
both sides.

This prevents a model from only being good inside one memorized four-agent
combination. The target is four individually useful players that can cooperate
with unfamiliar teammates.

The runner accepts repeated `--partner-model` arguments. Today the current
champion can be the only partner baseline; later the pool must include previous
champions, style variants and other independently trained policies.

## Anti-loop / context adaptation

Arena v2 does **not** punish repeated inputs by themselves. Holding a direction
can be the correct action.

It records a context failure when the policy keeps the same action while its
ball-relative context has materially changed (bearing or distance) for multiple
decision samples. It separately records when the action changes after that
context shift.

Metrics:

- `max_held_action_seconds`;
- `context_adaptations`;
- `context_misses`;
- `context_adaptation_rate`;
- `far_stall_rate`;
- `max_far_stall_seconds`.

This is deliberately stricter than simple action entropy: randomness is not
intelligence; adapting when the state changes is.

## Raw-policy rule

Arena v2 runs with safety recovery disabled.

Role leash, OOD recovery and boundary recovery are useful runtime protection,
but they may not create promotion evidence for the underlying policy. A future
guard-assisted comparison can be recorded separately.

## Frozen calibration v1

Arena v2 thresholds are frozen from GitHub Actions run `36266477245` at
calibration code SHA `46efb908a0e7323eff952287f9df85bb4d3c3824`.

The batch used all three frozen Candidate-D holdout sources, 16 scenarios per
source, 30-second rollouts, raw-policy-only execution, and a plug-and-play
partner population consisting of the live champion plus Candidates B and C.
It compared three policy classes:

1. champion vs itself as the identity control;
2. Candidate D;
3. an ablated control with all policy weights zeroed.

Observed behavior-gate results were champion-self 3/3, Candidate D 0/3 and
zero-policy 0/3. The most persistent Candidate-D regression was boundary drift
across all four roles, with additional role-target deviation in GK/DM/AM.

The frozen absolute thresholds are:

- team proxy match score >= 0.50;
- per-role plug-and-play proxy match score >= 0.50;
- absolute far-stall rate <= 0.90;
- absolute held-action duration <= 30 seconds;
- context-adaptation rate >= 0.25.

These thresholds are intentionally combined with the stricter paired-reference
regression checks. Proxy score alone is not sufficient: the zero-policy control
sometimes reached proxy scores above 0.50 while still failing the behavior
gate strongly.

The policy remains fail-closed unless the caller explicitly enables the frozen
thresholds with `calibrated=True` / `--calibrated`. The three full
champion-self sources were replayed with the frozen policy enabled and all
three remained structurally valid, behavior-pass and promotion-eligible.

Production champion pointers were not changed by calibration.

## Runner

```bash
NODE_PATH=/opt/haxlab/node_modules node tools/elite_closed_loop_arena_v2.js \
  --challenger /path/to/challenger/runtime-model.json \
  --champion /path/to/champion/runtime-model.json \
  --stadium /path/to/stadium.hbs \
  --scenarios /path/to/scenarios.json \
  --partner-model /path/to/champion/runtime-model.json \
  --max-scenarios 4 \
  --seconds 30 \
  --output /tmp/arena-v2.json
```

Add multiple `--partner-model` arguments to test deterministic random partner
lineups from a larger population.

## Promotion contract

A future live champion must eventually pass **both** tracks:

- strong 4-man team behavior;
- all four roles strong in plug-and-play evaluation.

A team-only specialist that collapses with unfamiliar teammates fails. An
individually active policy that damages coordinated team play also fails.
