from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "multisource-suite-v2.yml"
PROOF_WORKFLOW = (
    ROOT / ".github" / "workflows" / "multisource-suite-v2-workflow-provenance-proof.yml"
)
EXPECTED_CHECKOUT_SHA = "11d5960a326750d5838078e36cf38b85af677262"
EXPECTED_UPLOAD_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
USES_RE = re.compile(
    r"^\s*uses:\s*(actions/[A-Za-z0-9_.-]+)@([^\s#]+)",
    re.MULTILINE,
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _official_refs(path: Path = WORKFLOW) -> list[tuple[str, str]]:
    return USES_RE.findall(_text(path))


def test_multisource_v2_workflow_pins_all_official_actions() -> None:
    refs = _official_refs()
    assert refs, "expected official GitHub Action references"
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]
    assert [ref for action, ref in refs if action == "actions/upload-artifact"] == [
        EXPECTED_UPLOAD_SHA
    ]


def test_exact_head_precedes_regressions_and_live_freeze_inputs() -> None:
    text = _text()
    checkout = text.index("name: Checkout exact event SHA")
    verify = text.index("name: Verify exact validation head")
    regressions = text.index("name: Run multisource v2 regressions")
    freeze = text.index(
        "name: Freeze v2 manifest from existing immutable multisource evidence"
    )
    verify_manifest = text.index("name: Verify frozen manifest is self-consistent")
    upload = text.index("name: Upload frozen suite manifest")

    assert checkout < verify < regressions < freeze < verify_manifest < upload
    assert "ref: ${{ github.sha }}" in text
    assert "clean: true" in text
    assert "persist-credentials: false" in text
    assert "EXPECTED_SHA: ${{ github.sha }}" in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert 'if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then' in text


def test_multisource_v2_preserves_freeze_and_artifact_boundary() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 20" in text
    assert "branches:\n      - feature/arena-v2-current-main-20261004" in text
    assert '".github/workflows/multisource-suite-v2.yml"' in text
    assert "tests/test_scenario_source.py" in text
    assert "tests/test_multisource_suite.py" in text
    assert "tests/test_multisource_duel.py" in text
    assert "ROOT=/var/lib/haxlab/derived/evaluation/candidate-d-multisource-holdout-v1" in text
    assert "multisource-suite-v2-manifest.json" in text
    assert "name: multisource-evaluation-suite-v2" in text
    assert "retention-days: 30" in text


def test_manual_proof_is_source_only_and_live_head_bound() -> None:
    workflow = _text(PROOF_WORKFLOW)
    refs = _official_refs(PROOF_WORKFLOW)
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert [(action, ref) for action, ref in refs if not FULL_SHA_RE.fullmatch(ref)] == []
    assert [ref for action, ref in refs if action == "actions/checkout"] == [
        EXPECTED_CHECKOUT_SHA
    ]
    assert "ref: ${{ github.sha }}" in workflow
    assert "clean: true" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$HEAD_SHA" = "${GITHUB_SHA}"' in workflow
    assert 'git ls-remote --exit-code origin "${GITHUB_REF}"' in workflow
    assert "/var/lib/haxlab/" not in workflow
    assert "sudo -u haxlab" not in workflow
    assert "multisource-suite-v2-manifest.json" not in workflow
    assert "upload-artifact" not in workflow
