# HaxLab pipeline systemd equals-form option coverage

Data-pipeline service validation treats `--flag value` and `--flag=value` as equivalent forms accepted by Python CLI parsers. The previous static detector in `tests/test_data_pipeline_service_contract.py` only found options followed by whitespace, which could silently omit `--unknown=value` from its allowlist comparison.

The guard tokenizes the single logical `ExecStart` using shell quoting rules, normalizes an option token before its first `=`, and preserves original argument values. It detects unknown equals-form flags, reads mandatory known arguments in either spelling, rejects partial-option-prefix matches and fails closed if a unit has multiple `ExecStart` commands.

This change touches test-side static validation only. Existing service files, runtime argv parsing and immutable data evidence are unchanged. The separately integrated duplicate-option guard (#554/#555) continues to reject overriding scalar options rather than relying on the first occurrence of a checked flag.

Related issue: https://github.com/Zennay/Haxlab/issues/558.
