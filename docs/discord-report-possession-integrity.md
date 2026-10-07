# Discord report possession integrity

HaxLab treats parsed Discord match reports as evidence, so two-team possession
percentages must describe one coherent partition instead of merely passing
independent field bounds.

## Contract

When a report contains a possession line:

- Red and Blue percentages must each be within `[0, 100]`.
- Their sum must be within `0.5` percentage point of `100`.
- Display rounding remains valid; for example `50.2% / 49.7%` is accepted.
- Impossible evidence such as `150% / 20%` or `60% / 30%` fails with
  `invalid_possession_percentages`.

The tolerance mirrors the previously staged report-integrity contract and is
narrow enough to reject materially incomplete or contradictory possession
evidence while allowing ordinary decimal rounding.

## Scope

This contract only changes the report parser and focused report tests. It does
not change Discord import provenance, matching, canonical M0 publication,
runtime processing, learning, evaluation, or live/champion state.
