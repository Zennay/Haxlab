# Candidate I — touch-progression multitask contract

Candidate I is the first bounded Stage-D outcome-aware challenger. It is not a Candidate-H retry and adds no recovery router.

The only new learning mechanism is a training-only binary progression auxiliary head on Candidate-E h2. Labels come from state-pass-v4 touch-chain outcomes: the input window ends 6–11 ticks before a selected player's touch, and the target is positive iff the next-touch progression value is greater than zero.

The frozen dataset contains 119,184 train and 31,031 validation events with zero replay overlap against each other or the 142-replay human holdout. Full frozen-h2 development feasibility reached 0.71915 validation accuracy versus 0.59795 majority and AUC 0.77783; every role was above 0.77 AUC.

Candidate-E human training semantics remain unchanged. Auxiliary-only steps use separate Adam state, update only the shared representation plus the progression head, and never directly update direction/kick/future heads. The progression head is not serialized into the runtime policy.

The frozen auxiliary cadence is 56 updates × 256 samples = 14,336 draws per epoch, after each 100 human optimizer updates (Candidate-E has 5,675 human batches/epoch). Sampling is deterministic without replacement from the progression train fingerprints.

Before sealed holdout access, implementation must reproduce exact progression dataset counts/digests, meet the frozen progression learnability floors, remain inside preregistered human-validation regression floors, and pass the merged elite-gate evidence preflight at 660f1e9de5f93074f3b482ff9ff52c5da551e22c.

Promotion-v5 remains pristine and inaccessible until those gates and the sealed human holdout gate pass. Any blocking failure rejects Candidate I without retry or post-outcome tuning under this contract.
