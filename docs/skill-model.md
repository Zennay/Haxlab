# Player Skill Model

## Goal

Estimate how well a player actually plays, not merely how often their team wins.

Opponent strength is useful context, but a rating system that mainly says "you beat strong players, therefore you are strong" is circular and can be badly biased by teammates, roles and weak opposition.

HaxLab therefore separates **individual performance evidence** from **match context**.

## Skill is a vector, not one number

The internal representation should eventually contain dimensions such as:

- ball control and retention;
- progression and creation;
- passing/decision quality;
- finishing;
- defending;
- off-ball positioning;
- pressure and recovery;
- risk management;
- consistency.

A public-facing overall rating may be derived later, but the system should keep the dimensions and uncertainty underneath it.

## Evidence hierarchy

### 1. State-action quality

The strongest evidence is what a player does in a given state.

For a decision at time `t`, retain context such as:

- ball position and velocity;
- player positions and velocities;
- team possession;
- nearby pressure;
- open passing lanes;
- distance and angle to goal;
- score and time remaining;
- role/position estimate.

Then estimate whether the chosen action improved the team's state over a short horizon.

Examples:

- kept possession under pressure;
- advanced the ball into a more valuable area;
- created a shot or numerical advantage;
- chose a safe outlet instead of forcing a turnover;
- closed a dangerous lane;
- recovered after an overcommit.

This makes the rating about **decision quality and execution**, not just final score.

### 2. Possession-level outcomes

Aggregate actions into possessions/sequences:

- possession value added;
- dangerous-state creation;
- turnover cost;
- defensive stop value;
- transition success;
- shot creation/concession.

This gives more stable evidence than isolated touches.

### 3. Match-level performance

Match summaries are useful only after normalizing for:

- minutes played;
- role;
- team strength;
- opponent strength;
- score state;
- match quality;
- unusual disconnects/substitutions.

### 4. Result context

Wins, losses and strength of opposition are still useful, but they are one feature among many.

They should adjust confidence and context, not dominate the rating.

## Context correction

A player's observed performance should be modeled conceptually as:

```text
observed performance
= player skill
+ teammate context
+ opponent context
+ role/context
+ match-state effects
+ noise
```

The system should try to infer the latent player component rather than assigning all observed outcome to the player.

## Teammate correction

A player can look good because three elite teammates constantly create easy states.

Useful corrections include:

- teammate skill estimates;
- share of team possessions/actions;
- performance when lineups change;
- marginal contribution versus team baseline;
- repeated performance across different teammate combinations.

## Opponent correction

A player farming weak opponents should not automatically become elite.

Track:

- opponent team skill distribution;
- quality of direct pressure/defensive matchups;
- expected performance against that opposition;
- residual performance above/below expectation.

Example:

```text
expected retention under this pressure = 78%
actual retention = 91%
residual = +13%
```

The residual is more informative than the raw 91%.

## Role awareness

Do not compare every player with the same metric weights.

A goalkeeper/last defender, midfielder and forward can all be excellent while producing different stat profiles.

The role model can begin heuristic and later become learned from positional/action clusters.

## Match-quality weighting

Every observation inherits a data-quality weight.

Examples that reduce weight:

- incomplete replay;
- AFK/disconnect;
- extreme blowout;
- very short participation;
- incompatible/custom stadium;
- suspiciously low activity;
- uncertain player identity.

Low-quality data can remain useful for general analytics while being excluded from elite imitation datasets.

## Uncertainty

Every player estimate must have uncertainty.

A player with 4 excellent matches should not be treated as equally known as a player with 400 excellent matches.

Store at least:

- sample count;
- effective weighted sample count;
- estimate;
- uncertainty/confidence interval;
- last update;
- role confidence.

## V0 implementation

Do not begin with a giant black-box model.

V0 can compute interpretable features and maintain conservative rolling estimates:

1. normalize metrics per possession/minute;
2. adjust for role;
3. attach match-quality weights;
4. calculate teammate/opponent context;
5. calculate residual performance versus expected baseline;
6. aggregate with shrinkage toward population mean;
7. expose uncertainty.

## V1+ implementation

Once enough data exists, move toward a hierarchical probabilistic model or learned value model that jointly estimates:

- latent player skill dimensions;
- teammate effects;
- opponent effects;
- role;
- state difficulty;
- uncertainty.

The key invariant remains:

> Player skill is inferred from repeated quality of play in context, not from a leaderboard of who happened to beat whom.
