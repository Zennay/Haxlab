# Multisource duel top-level gate input contract

The multisource duel gate is a promotion boundary. Invalid top-level inputs and policy values must return a conservative non-eligible decision rather than raising or silently weakening the gate.

## Contract

`decide_multisource_duel_gate()`:
- requires the top-level payload to be a mapping;
- requires a `MultisourceDuelPolicy` object;
- requires a `DuelGatePolicy` object;
- requires `MultisourceDuelPolicy.minimum_sources` to be a native Python integer >= 1;
- rejects booleans, floats, strings, zero and negative minimum-source values;
- uses only the validated minimum for source-count enforcement.

A valid explicit policy such as `minimum_sources=1` remains supported. A stricter valid minimum remains enforceable.

## Why

Python treats booleans as integers. Without explicit validation, `minimum_sources=False` behaves like zero and can weaken a mandatory independent-source gate. Wrong object types can also raise before a fail-closed decision exists. Promotion boundaries must turn malformed configuration into negative evidence, never weaker acceptance criteria or an uncaught exception.
