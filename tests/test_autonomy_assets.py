from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_autonomy_timer_is_persistent_and_continuous():
    text = (ROOT / "deploy/haxlab-autonomy.timer").read_text()
    assert "OnUnitActiveSec=1min" in text
    assert "Persistent=true" in text
    assert "haxlab-autonomy.service" in text


def test_autonomy_tick_wires_generation_executor():
    text = (ROOT / "deploy/haxlab-autonomy-tick.sh").read_text()
    assert "refresh_skill" in text
    assert "build_dataset_manifest" in text
    assert "train_baseline_challenger" in text
    assert "haxlab-generation-loop" in text
    assert "max-generations-per-tick 1" in text
    assert "failure" in text.lower()


def test_pipeline_failures_do_not_block_usable_evidence():
    text = (ROOT / "deploy/haxlab-autonomy-tick.sh").read_text()
    assert "ANALYSIS_OK == 0" in text
    assert "Failed/corrupt source replays must not prevent safe deterministic work" in text
    assert "analysis_failed=" in text


def test_vps_update_enables_autonomy_timer_and_generation_cli():
    text = (ROOT / "deploy/update-vps.sh").read_text()
    assert "haxlab-autonomy.timer" in text
    assert "haxlab-generation-loop" in text
