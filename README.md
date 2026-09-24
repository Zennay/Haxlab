# HaxLab

HaxLab is a replay intelligence and learning platform for HaxBall.

The project starts with a deliberately simple rule:

> Do not train on everything just because it exists.

Discord exports and `.hbr2` replays are treated as raw evidence. HaxLab parses them into a canonical dataset, scores match quality, estimates player skill with uncertainty, produces match/player/team analytics, and later uses only suitable data to train and evaluate AI agents.

## Product layers

1. **Replay ingestion**
   - Import DiscordChatExporter JSON and downloaded assets.
   - Discover and hash `.hbr2` files.
   - Match Discord reports to replays.
   - Preserve provenance and detect duplicates.

2. **Match intelligence**
   - Frames, events, possession, touches, passes, shots, turnovers and goals.
   - Player heatmaps and position profiles.
   - Team shape, spacing and recurring tactical patterns.

3. **Quality and skill**
   - Reject corrupted or unusable matches.
   - Weight usable matches by quality.
   - Estimate player skill from individual actions and outcomes, not only wins/losses.
   - Model uncertainty and context such as opponent quality, teammates, role, score state and match quality.

4. **AI coach**
   - Convert measured patterns into evidence-backed player and team advice.
   - Keep every recommendation traceable to underlying metrics/events.

5. **Learning player**
   - Behavioral cloning from strong human examples.
   - Self-play and reinforcement learning.
   - Challenger-versus-champion evaluation.
   - Promote a new model only when it improves on frozen benchmarks.

## Skill estimation principle

Opponent strength matters, but it is only one context variable.

A player is not considered strong simply because they beat high-rated players, and not weak simply because they lost with a weak team. HaxLab aims to measure how well a player acts in comparable game states.

Examples of evidence:

- ball retention under pressure;
- decision quality when multiple passing/dribbling options exist;
- progression without unnecessary turnovers;
- shot quality and finishing relative to opportunity;
- defensive stops and recovery positioning;
- spacing and off-ball positioning;
- mistakes that directly create dangerous states;
- consistency over many possessions and matches.

See [docs/skill-model.md](docs/skill-model.md).

## Self-improving loop

```text
new replays
  -> validate and score data quality
  -> update player/context estimates
  -> build versioned training dataset
  -> train challenger
  -> evaluate against frozen tests + previous champions
  -> promote or reject
  -> mine failures
  -> add hard cases to curriculum
  -> repeat
```

Newer is never automatically better.

## Repository layout

```text
docs/
  architecture.md
  data-model.md
  skill-model.md
  learning-loop.md
  evaluation.md
src/haxlab/
  ingestion/
  replay/
  features/
  quality/
  skill/
  analysis/
  coaching/
  learning/
  evaluation/
tests/
configs/
data/
```

## Current milestone

**M0 — Trusted Dataset Pipeline**

The first milestone is complete when a Discord export can be imported repeatedly without changing raw data or creating duplicate records, every replay has a stable hash, report-to-replay matching is explainable, and parse failures are visible instead of silently skipped.

No AI training is required for M0.
