from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_actions_control_exposes_archive_audit() -> None:
    script = (ROOT / "deploy" / "haxlab-actions-control.sh").read_text(
        encoding="utf-8"
    )
    assert "archive-audit)" in script
    assert "haxlab-audit-archive" in script
    assert 'ionice -c 3 nice -n 10 haxlab-audit-archive' in script
    assert '--state-db "${STATE_DB}"' in script


def test_vps_workflow_exposes_archive_audit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "vps-control.yml").read_text(
        encoding="utf-8"
    )
    assert "          - archive-audit" in workflow
    assert (
        "sudo /usr/local/sbin/haxlab-actions-control archive-audit 25"
        in workflow
    )
