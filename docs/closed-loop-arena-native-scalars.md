# Closed-loop Arena native scalar evidence boundary

The Arena v2 evaluator consumes evidence that ultimately originates from JSON artifacts. Its scalar evidence boundary therefore accepts only JSON-native Python scalar representations:

- integer counters: exact built-in `int` (never `bool`, subclasses, strings, floats, or custom coercibles);
- numeric rates/durations: exact built-in `int` or `float` (never `bool`, subclasses, strings, or custom coercibles);
- all accepted numbers must remain finite and satisfy the existing field bounds.

This matters because `isinstance(value, (int, float))` also accepts Python subclasses. A hostile subclass can override `__float__` or `__int__`, causing evidence validation to execute arbitrary conversion behavior or raise outside the deterministic fail-closed decision model.

The evaluator now checks exact native types before conversion. Its normalization helpers likewise refuse non-native scalar values, so invalid evidence cannot reach conversion hooks after a structural failure has already been recorded.

This change does not alter frozen thresholds, Arena semantics for valid JSON evidence, model/champion state, promotion state, or calibration workflow behavior. It only tightens the evidence type boundary tracked by issue #421.
