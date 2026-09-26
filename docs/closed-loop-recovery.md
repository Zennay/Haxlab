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


## A/B protocol

Replay-seeded evaluation runs the same existing champion, stadium, scenarios,
sides and scripted opponents twice:

1. recovery disabled;
2. recovery enabled.

The comparator reports movement, near-ball rate, progression, territory,
side-gap and recovery override rate. Runtime recovery is only considered for
promotion when it improves at least one closed-loop metric without meaningful
regression elsewhere.


## Recovery sweep

The replay-seeded benchmark evaluates three recovery strengths against the exact
same champion and scenarios:

- conservative: 240 px / 6 stalled decisions / 2 recovery decisions;
- medium: 180 px / 5 stalled decisions / 3 recovery decisions;
- aggressive: 120 px / 3 stalled decisions / 5 recovery decisions.

A selector may promote only a candidate that passes all closed-loop safety
checks. Among passing candidates it prefers the lowest fallback usage.
