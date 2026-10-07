# Duel gate exact-type boundary contract

The duel gate consumes JSON evidence and a frozen policy object at a live-promotion boundary. It must reject behavior-bearing subclasses before calling payload methods or reading policy attributes.

## Contract

`decide_duel_gate()` requires:
- an exact native `dict` duel payload;
- an exact `DuelGatePolicy` object.

A `dict` subclass is rejected before `.get()` can execute. A `DuelGatePolicy` subclass is rejected before any threshold attribute can be read. Both cases return the existing conservative invalid-object decision.

This does not change any metric threshold or valid plain-dict/plain-policy behavior.
