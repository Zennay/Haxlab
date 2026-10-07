# Evaluation host resource/scheduler state isolation

HaxLab evaluation gates are evidence functions. Re-running the same gate in the same
process must not change later results by silently changing host resource limits,
priority, scheduler policy, or CPU affinity.

## Invariant

Python modules under `src/haxlab/evaluation/` must not mutate host resource or
scheduler state.

Rejected APIs:

- `resource.setrlimit(...)`;
- `resource.prlimit(..., limits)` when a replacement limit is supplied;
- `os.nice(...)`;
- `os.setpriority(...)`;
- `os.sched_setaffinity(...)`;
- `os.sched_setscheduler(...)`;
- `os.sched_setparam(...)`.

Read-only inspection remains valid:

- `resource.getrlimit(...)`;
- two-argument `resource.prlimit(pid, resource)` (and explicit `limits=None`);
- `os.getpriority(...)`;
- `os.sched_getaffinity(...)`;
- `os.sched_getscheduler(...)`;
- `os.sched_getparam(...)`.

## Why this is separate from process-state isolation

The existing process-state contracts #200/#201 and alias follow-up #369/#371 own
cwd, environment, import state, signals, umask, locale, and interpreter hooks.
They do not cover operating-system resource ceilings or scheduler/affinity state.

Those controls are dangerous in evaluation code because they persist outside one
gate invocation. A gate could lower CPU/file/memory ceilings, change process
priority, or pin the process to a different CPU set and thereby make later gates
depend on call order. On the shared self-hosted runner this can also degrade
unrelated validation work.

## Static enforcement

`tests/test_evaluation_resource_scheduler_state_contract.py` recursively scans
the evaluation package and resolves:

- normal and aliased module imports;
- direct imports of mutating callables;
- simple/annotated/named assignment aliases;
- bound callable aliases;
- constant `getattr(...)` spellings;
- lexical function/argument shadowing.

`resource.prlimit` is treated specially: the read-only form is allowed, while a
third positional limit or non-`None` `limits=` argument is rejected.

## Acceptance

The dedicated self-hosted workflow must prove on the exact live branch head:

1. exact checkout and branch-head identity;
2. Python compilation;
3. the focused resource/scheduler contract;
4. adjacent canonical Arena-v2 evaluation regressions;
5. marker `HAXLAB_EVALUATION_RESOURCE_SCHEDULER_STATE_RESULT=green`.

This is an additive validation lane only. It does not change evaluation product
logic, thresholds, evidence, models, champion/promotion state, or canonical
Arena-v2 workflow state.
