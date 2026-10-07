from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/closed-loop-arena-v2-calibration.yml")
TIMED_OUT_HEAD = "1c186d5d8927ab55722615631e0e61e5bda56971"
RUNTIME_SURFACE = (
    "package.json",
    "tools/elite_closed_loop_arena_v2.js",
    "tools/elite_policy_runtime.js",
    "tools/elite_features.js",
    "tools/elite_tactics.js",
    "tools/sandbox_replay_start.js",
    "tools/sandbox_neutral_start.js",
    "src/haxlab/evaluation/calibration_resume.py",
    "src/haxlab/evaluation/closed_loop_arena.py",
)


def _run_source_block(text: str) -> str:
    start = text.index("          run_source() {")
    end = text.index("\n          for LABEL in champion-self candidate-d weak-zero;", start)
    return text[start:end]


def test_cross_sha_resume_is_pinned_to_known_timed_out_head() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert f'REUSE_SOURCE_SHA="{TIMED_OUT_HEAD}"' in text
    assert 'REUSE_OUT="/var/lib/haxlab/derived/evaluation/' in text
    assert '${REUSE_SOURCE_SHA:0:12}' in text
    assert 'git merge-base --is-ancestor "$REUSE_SOURCE_SHA" "$GITHUB_SHA"' in text


def test_cross_sha_resume_requires_identical_rollout_runtime_surface() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    guard_start = text.index('git diff --quiet "$REUSE_SOURCE_SHA" "$GITHUB_SHA" --')
    guard_end = text.index("; then", guard_start)
    guard = text[guard_start:guard_end]

    for path in RUNTIME_SURFACE:
        assert path in guard, f"cross-SHA reuse guard lost runtime dependency: {path}"


def test_cross_sha_result_is_revalidated_before_copy_and_decision() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    block = _run_source_block(text)

    previous = block.index('"$REUSE_RESULT"')
    validator = block.index(
        "python -m haxlab.evaluation.calibration_resume",
        previous,
    )
    copy = block.index('cp "$REUSE_RESULT" "$RESULT"', validator)
    marker = block.index("ARENA_CALIBRATION_SOURCE_REUSED_CROSS_SHA", copy)
    decision = block.index(
        "python -m haxlab.evaluation.closed_loop_arena",
        marker,
    )

    assert previous < validator < copy < marker < decision
    assert '--challenger "$CHALLENGER"' in block[validator:copy]
    assert '--champion "$ARENA_LIVE"' in block[validator:copy]
    assert '--stadium "$SOURCE/stadium.hbs"' in block[validator:copy]
    assert '--scenarios "$SOURCE/scenarios.json"' in block[validator:copy]
    assert "--max-scenarios 16" in block[validator:copy]
    assert "--seconds 30" in block[validator:copy]
    assert "--sample-every 6" in block[validator:copy]
    assert "--plug-repeats 1" in block[validator:copy]
    assert "--seed 1337" in block[validator:copy]


def test_cross_sha_resume_has_full_history_for_ancestry_and_diff_guard() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    checkout = text.index("name: Checkout exact event SHA")
    candidate = text.index("name: Checkout frozen Candidate D source", checkout)
    block = text[checkout:candidate]

    assert "ref: ${{ github.sha }}" in block
    assert "clean: true" in block
    assert "fetch-depth: 0" in block
