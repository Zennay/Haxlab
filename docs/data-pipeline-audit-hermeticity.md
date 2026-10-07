# Data-pipeline audit hermeticity contract

HaxLab treats the standalone `*_audit.py` modules in `haxlab.ingestion`, `haxlab.learning`, and `haxlab.runtime` as verifiers of already-published evidence. Importing a verifier must therefore be inert: importing the module is not allowed to mutate the filesystem, launch child processes, open network-capable dependencies, or emit output.

## Enforced boundary

`tests/test_data_pipeline_audit_hermeticity.py` discovers every `*_audit.py` below the three data-pipeline packages and applies two independent checks:

1. **Static boundary:** direct network/process imports, dynamic import surfaces, and process-launch calls are rejected. Local deterministic file and SQLite reads remain valid.
2. **Runtime import boundary:** every discovered module is imported in a fresh isolated Python process with bytecode writes disabled, an empty working directory, and an audit hook that rejects write-capable opens, filesystem metadata/path mutation, subprocess activity, and socket activity. Import-time stdout/stderr also fails the contract.

The test includes a self-test that representative forbidden imports and calls are detected, so weakening the scanner accidentally cannot silently turn the policy into a no-op.

## Scope

This contract does not change the behavior of any existing auditor or producer. It does not modify runtime state, scanner/archive logic, M0 publication, training selection/shards, evaluation, models, or champion state. The goal is to keep verification modules side-effect-free at import time while allowing their explicitly invoked audit functions to read local evidence deterministically.
