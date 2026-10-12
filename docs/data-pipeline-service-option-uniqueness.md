# Data-pipeline systemd CLI option uniqueness

The HaxLab ingestion, worker, and analyzer systemd units use Python command-line options in `ExecStart`. A repeated scalar long option is an unsafe configuration ambiguity: for example, `--interval 5 --interval inf` can pass a static first-match validator while `argparse` receives the later overriding value.

## Source-only invariant

The independent regression in `tests/test_data_pipeline_service_option_uniqueness.py` checks the three named service units and rejects repeated long option names in the logical `ExecStart` command. It treats `--option=value` and `--option value` as the same option identity and fails closed if an expected service has zero or multiple `ExecStart` lines.

Line-continuation whitespace and distinct long options remain valid. Synthetic tests cover both override spellings, multiple repeated options, missing commands and ambiguous commands.

This contract does not execute systemd, inspect the host's deployed unit overrides, change a unit, dispatch a runner, or rewrite any runtime value. In-host live config/effective `systemctl show` checks are a separate deployment concern.

Related issue: https://github.com/Zennay/Haxlab/issues/554.
