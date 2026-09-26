# Closed-loop recovery guard

The elite behavior-cloning policy remains the primary controller.

The recovery guard activates only after an agent predicts no movement for at
least three consecutive policy decisions while the ball is at least 120 px
away. It then applies a short five-decision GK/DM/AM/ST recovery trajectory
before returning control to the learned policy.

This is intentionally a narrow safety layer for covariate-drift / inactivity,
not a replacement scripted player. The policy continues to receive states and
advance its temporal history while recovery is active.

Evaluation must compare the existing champion artifact before/after this runtime
layer on both:

- neutral-start local 4v4 sandbox matches;
- replay-seeded closed-loop scenarios from real match states.

Recovery override count/rate is reported so reliance on the fallback remains
observable.
