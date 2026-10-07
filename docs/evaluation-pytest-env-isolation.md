# Evaluation pytest environment isolation

Exact-head evaluation evidence must reflect repository code, not accidental state
inside the long-lived self-hosted runner environment.

## Incident motivating this contract

The canonical runtime-budget proof for issue #92 / PR #427 reached its focused
pytest step, but the runner's shared `/opt/haxlab/.venv` no longer contained
pytest. Checkout and compilation were already green. That failure therefore
described mutable runner-environment drift rather than the candidate contract.

## Invariant

Evaluation-related GitHub workflows that execute pytest must:

1. create an isolated venv during the job before pytest is invoked;
2. execute pytest from a venv-local executable or venv-local Python;
3. never execute pytest through `/opt/haxlab/.venv`;
4. never fall back to bare `pytest` or system `python -m pytest`.

The production/service venv may still be used for real HaxLab runtime commands
where that environment is part of the runtime contract. This validation rule is
specifically about pytest-based acceptance evidence.

## Scope

`tests/test_evaluation_pytest_env_isolation_contract.py` scans evaluation,
Arena, calibration, multisource, promotion, duel, scenario and closed-loop
workflow files. Workflows without pytest are unaffected.

The scanner also has adversarial regressions for:

- the shared `/opt/haxlab/.venv`;
- bare pytest;
- system Python `-m pytest`;
- pytest executed before venv creation;
- valid venv-local pytest and `python -m pytest` execution.

## Acceptance

The branch-scoped proof must verify the exact immutable branch head, build its
own isolated proof venv, compile the focused contract, run it, and publish a
green marker.

This is evaluation-validation infrastructure only. It does not alter Arena
logic, thresholds, evidence payloads, calibration inputs, models, champion
state, promotion state, or canonical evaluation workflows.
