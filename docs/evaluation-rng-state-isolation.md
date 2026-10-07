# Evaluation RNG-state isolation

## Purpose

Canonical Arena-v2 evaluation must not mutate process-global pseudo-random-number-generator state. A gate that reseeds or replaces a shared RNG state can change later gate behavior even when every later call uses otherwise deterministic inputs.

This is distinct from the ambient nondeterminism contract (#147/#148): that contract prevents evaluation decisions from depending on ambient randomness. This contract prevents one evaluation call from changing the RNG state seen by later calls.

## Guarded state

The recursive AST contract rejects:

- Python module-global RNG mutation through `random.seed` / `random.setstate`, including direct mutation of the module singleton `random._inst`;
- legacy NumPy global RNG mutation through `numpy.random.seed` / `numpy.random.set_state`, including the legacy `mtrand._rand` singleton;
- Torch process/global generator mutation through `torch.manual_seed`, `torch.seed`, `torch.set_rng_state`, the equivalent `torch.random.*` calls, `torch.default_generator`, and CUDA global/default-generator seed/state setters.

Import aliases, module aliases, assignment/annotated/named aliases, tuple/list unpacking, bound callable aliases, walrus expressions, constant-`getattr(...)` spellings, and indexed CUDA default-generator references are resolved before checking. Function/lambda/comprehension lexical shadowing is preserved. Wildcard imports from tracked RNG modules are rejected because their provenance is ambiguous.

## Preserved deterministic usage

Local generator objects remain allowed. Examples include:

- `random.Random(seed)`;
- `numpy.random.default_rng(seed)`;
- a local `torch.Generator()` followed by `.manual_seed(seed)`;
- read-only state inspection such as `random.getstate()`, `numpy.random.get_state()`, `torch.get_rng_state()`, and CUDA state getters.

The invariant is process-state isolation, not a ban on deterministic random algorithms.

## Scope

This lane is additive validation only. It changes no evaluation product implementation, thresholds, policies, evidence, model/champion state, promotion state, canonical Arena-v2 workflow, or existing owner files.

It is file- and semantics-disjoint from:

- #148 ambient nondeterminism;
- #201/#371 OS/interpreter process-state mutation;
- #351 context/thread-local state;
- #399/#402 numeric runtime configuration;
- #398/#403 host resource scheduling;
- #404 garbage-collector process state.

Acceptance requires exact/live-head self-hosted proof of this focused contract plus adjacent canonical Arena-v2 regressions.
