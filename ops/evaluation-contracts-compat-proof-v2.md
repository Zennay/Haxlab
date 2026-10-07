# Evaluation validation compatibility proof v2

Execution-only synthetic compatibility carrier for issue #238.

Canonical Arena-v2 base:
- `ee5ab508e9d8504de12b7cbb5fdeca43a58ae1a4`

Exact additive contract inputs:
- #139 optimization safety: `df5184b54c40a0df4f73a2adb1923e8a85e3f87e`
- #148 ambient determinism: `69646fd0817b162be31add2bf8e87be3cb9b4fc7`
- #153 network/subprocess hermeticity: `9e82831b22e000c773284223bfa53a02f116a87f`
- #157 safe deserialization: `8d1a1039bd985b1d4860cc219d205e9d2e2fe9e6`
- #161 control-plane import isolation: `c738bad12c1092812ac1d1ad6b9f02d8b3df262c`
- #162 import purity: `17d21965140f61736b16fa9dba23c823346b58d2`
- #166 exception boundary: `be70c0158b8c0aad90166997ef49e19b9bccf4d1`
- #168 mutable global state: `9f16cb83e1b3aad9242cf04ead6291b3f40bb982`
- #171 dynamic loading: `5ab757bd76e2594b82cc936782455be9844a4fba`
- #201 process-state isolation: `523694b02be5262091ec2d61a8f6b06e4c6fe563`
- #209 host-process termination/replacement: `87f2b01d6975f5ce90e6a602c2ac1eea77eec887`
- #212 hidden/background execution: `5445483fc0301d606b3dd831288ca11bd21578cc`
- #215 runtime import cycles: `b9ae444bd437a07667ab80086cc0ff0d22bf4ace`
- #220 immutable evidence-object bypasses: `0d75bda2a496a1d84ea9f2b01084fef56d374cff`
- #222 foreground liveness/non-interactivity: `8fa40c0748f6130d2ac3f51be4dda9b5bb0206e8`

The workflow materializes only each PR's additive contract test file into an untouched canonical checkout. It does not merge product code, workflows, docs, thresholds, models, champion pointers, or owner branches.

It then runs all 15 contract tests together plus adjacent canonical Arena-v2 Python regression suites. The owner-reserved syntax-broken `tests/test_calibration_workflow_runtime_budget.py` from #92 is deliberately not collected.

This branch is proof-only and must not be merged into PR #19. A green result is compatibility evidence, not merge or champion-promotion authorization.
