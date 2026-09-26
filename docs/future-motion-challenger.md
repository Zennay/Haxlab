# Future-motion challenger

The current elite champion predicts the immediate human key state well offline,
but replay-seeded rollouts show that exact one-step imitation can become too
stationary under closed-loop covariate shift.

The challenger adds an auxiliary learned target: the player's displacement
direction five 10 Hz samples into the future (about 0.5 seconds). The target is
derived directly from elite replay trajectories already stored in the training
shards, so replay decoding does not need to be repeated.

The immediate movement and kick heads remain primary. The future head is only
eligible to break a stationary action when:
- the model actually contains the learned future head;
- immediate movement is 0,0;
- the ball is at least 80 px away;
- future-direction confidence is at least 0.45.

Promotion is evaluated against the existing champion on identical replay-seeded
states. Immediate frozen-holdout skill must remain essentially intact and
closed-loop progression/territory may not materially regress.


## Concurrency note

Future-challenger runs are isolated from ordinary branch pushes so unrelated
scenario/audit commits cannot cancel an active model training job.


## Isolated experiment run

The challenger is trained from the frozen champion shard indices on its own
experiment branch. This prevents unrelated scenario, audit or documentation
pushes on the main feature branch from cancelling the long-running training job.
