from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "deploy" / "haxlab-actions-control.sh"
LIVE_BOT = ROOT / "tools" / "live_haxball_bot.js"


def _case_block(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_champion_runtime_uses_promoted_pointer_resolver() -> None:
    text = CONTROL.read_text(encoding="utf-8")
    block = _case_block(text, "  champion-runtime)", "  live-play-probe)")

    assert "from haxlab.live.inference import resolve_version_dir" in block
    assert "version_dir = resolve_version_dir(root)" in block
    assert 'root.glob("versions/*/metrics.json")' not in block


def test_controlled_live_start_does_not_override_promoted_version() -> None:
    text = CONTROL.read_text(encoding="utf-8")
    block = _case_block(text, "  live-play-start)", "  live-play-stop)")

    assert "HAXLAB_CHAMPION_VERSION" not in block
    assert "systemctl restart haxlab-live-bot.service" in block


def test_controlled_live_host_does_not_override_promoted_version() -> None:
    text = CONTROL.read_text(encoding="utf-8")
    block = _case_block(text, "  live-host-start)", "  live-host-status)")

    assert "HAXLAB_CHAMPION_VERSION" not in block
    assert "systemctl restart haxlab-live-bot.service" in block

def test_live_bot_cannot_forward_environment_version_override() -> None:
    text = LIVE_BOT.read_text(encoding="utf-8")

    assert "HAXLAB_CHAMPION_VERSION" not in text
    assert 'pythonArgs.push("--version"' not in text

