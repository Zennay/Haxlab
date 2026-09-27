from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_autonomy_timer_is_persistent_and_bounded():
    text = (ROOT / "deploy/haxlab-autonomy.timer").read_text()
    assert "OnUnitActiveSec=10min" in text
    assert "Persistent=true" in text
    assert "haxlab-autonomy.service" in text


def test_autonomy_tick_preserves_research_gates():
    text = (ROOT / "deploy/haxlab-autonomy-tick.sh").read_text()
    assert "refresh_skill" in text
    assert "build_dataset_manifest" in text
    assert "NEEDS_AI" in text
    assert "elite_closed_loop_arena_v2.js" in text
    assert "autonomous challenger mutation remains fail-closed" in text


def test_vps_update_enables_autonomy_timer():
    text = (ROOT / "deploy/update-vps.sh").read_text()
    assert "haxlab-autonomy.timer" in text
    assert "chmod 0755" in text
