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

## Distribution-drift telemetry

The Python and Node runtimes also report mean/max absolute input z-score for the
current frame. These OOD values are diagnostic only in the first recovery A/B:
recovery is still triggered solely by sustained inactivity at large ball
distance. This keeps the experiment attributable. OOD-triggered recovery can be
introduced only if the stall-only sweep leaves a measurable closed-loop gap.


## Latest replay-seeded recovery sweep

Using the current champion on the same 16 side-swapped replay-seeded matches:

| mode | movement | progression share | territory | near-ball | override rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| disabled | 33.71% | 58.03% | 49.21% | 0.41% | 0.00% |
| conservative | 85.56% | 48.99% | 50.65% | 0.78% | 1.35% |
| medium | 91.35% | 52.64% | 49.60% | 0.61% | 1.24% |
| aggressive | 95.40% | 44.39% | 49.21% | 0.32% | 1.22% |

No scripted recovery profile passes the promotion gate. Medium is the least harmful
trade-off, but its progression share still drops by about 5.4 percentage points,
beyond the allowed 3-point regression. The live runtime therefore keeps scripted
recovery disabled.

The next challenger is learned future-motion: predict where the elite human will
move roughly 0.5 seconds ahead and use that learned signal only when the immediate
policy stalls.
