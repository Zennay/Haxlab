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

## Initial fail-closed calibration

`haxlab.evaluation.closed_loop_arena` ships with initial structural/behavior
checks, but promotion eligibility remains disabled until thresholds have been
calibrated and frozen against real reference runs.

The first calibration set is:

1. current live/legacy champion;
2. Candidate D after its active training run finishes;
3. at least one deliberately weak or ablated policy where available.

Only after those distributions are inspected should the policy be marked
`calibrated=True` and wired into the champion promotion workflow.

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
