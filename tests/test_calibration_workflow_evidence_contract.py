from pathlib import Path


WORKFLOW = Path(".github/workflows/closed-loop-arena-v2-calibration.yml")


def test_calibration_evidence_collection_is_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    collect = text.index("name: Collect calibration evidence")
    upload = text.index("name: Upload calibration evidence")
    block = text[collect:upload]

    assert "if: always()" in block
    assert "set -euo pipefail" in block
    assert "set -u\n" not in block


def test_calibration_evidence_upload_requires_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    upload = text.index("name: Upload calibration evidence")
    block = text[upload:]

    assert "uses: actions/upload-artifact@v4" in block
    assert "if-no-files-found: error" in block
    assert "if-no-files-found: warn" not in block
