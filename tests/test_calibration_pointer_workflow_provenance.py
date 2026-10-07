from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github" / "workflows" / "calibration-pointer-invariant-validation.yml"
)
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "calibration-pointer-workflow-provenance-proof.yml"
)
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _official_refs(path: Path = WORKFLOW) -> list[tuple[str, str]]:
    return USES_RE.findall(_text(path))


def test_calibration_pointer_workflow_pins_official_actions() -> None:
    refs = _official_refs()
    assert refs, "expected at least one official GitHub Action reference"
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]


def test_exact_head_is_verified_before_live_provenance_inputs() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact validation head")
    verify = text.index("name: Verify exact validation head")
    live_inputs = text.index(
        "name: Verify calibration provenance inputs are readable and stable"
    )

    assert checkout < verify < live_inputs
    assert "ref: ${{ github.sha }}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text


def test_calibration_pointer_workflow_preserves_live_validation_boundary() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 15" in text
    assert "validation/calibration-pointer-invariant-20261006" in text
    assert "CURRENT=/var/lib/haxlab/derived/champions/elite-player/current.json" in text
    assert "LIVE=/var/lib/haxlab/derived/champions/elite-player/live.json" in text
    assert "EXPECTED_FROZEN_MANIFEST_SHA=" in text
    assert "CALIBRATION_POINTER_INPUTS_STABLE" in text


def test_manual_proof_is_immutable_and_live_head_bound() -> None:
    workflow = _text(PROOF_WORKFLOW)
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in workflow
    )
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$HEAD_SHA" = "${GITHUB_SHA}"' in workflow
    assert 'git ls-remote --exit-code origin "${GITHUB_REF}"' in workflow
    assert 'if [ "$LIVE_SHA" != "${GITHUB_SHA}" ]; then' in workflow
