from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.runtime.finalize as finalize_module
from haxlab.learning.selector import MANIFEST_SCHEMA
from haxlab.runtime.finalize import finalize_analysis_if_ready
from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


def test_finalize_requires_complete_analysis(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "r.hbr2"
    replay.write_bytes(b"x")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256="a" * 64,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256="a" * 64,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )

        result = finalize_analysis_if_ready(state, derived_root=derived)

    assert result["status"] == "not_ready"
    assert not (derived / CURRENT_ANALYZER_VERSION / "_complete.json").exists()


def test_finalize_writes_versioned_snapshot_and_manifest(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "r.hbr2"
    replay.write_bytes(b"x")
    sha = "b" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    (analysis_root / f"{sha}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(analysis_root / f"{sha}.json"),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )

        first = finalize_analysis_if_ready(state, derived_root=derived)
        second = finalize_analysis_if_ready(state, derived_root=derived)

    leaderboard = (
        derived / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    )
    completion = derived / CURRENT_ANALYZER_VERSION / "_complete.json"

    assert first["status"] == "finalized"
    assert second["status"] == "already_finalized"
    assert leaderboard.exists()
    assert completion.exists()

    leaderboard_payload = json.loads(leaderboard.read_text(encoding="utf-8"))
    completion_payload = json.loads(completion.read_text(encoding="utf-8"))

    assert leaderboard_payload["analysis_version"] == CURRENT_ANALYZER_VERSION
    assert leaderboard_payload["rows"] == []
    assert completion_payload["analysis_ok"] == 1
    assert completion_payload["analysis_failed"] == 0
    assert completion_payload["analysis_pending"] == 0
    assert completion_payload["analysis_ticks_reconstructed"] == 600


def test_finalize_refreshes_when_dataset_grows(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"

    with RuntimeState(db) as state:
        for index, char in enumerate(("c", "d"), start=1):
            sha = char * 64
            replay = tmp_path / f"{char}.hbr2"
            replay.write_bytes(b"x")

            analysis_root = (
                derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
            )
            analysis_root.mkdir(parents=True, exist_ok=True)
            output = analysis_root / f"{sha}.json"
            output.write_text(
                json.dumps(
                    {
                        "schemaVersion": 4,
                        "totalFrames": 600,
                        "simulation": {"sampleEveryTicks": 6},
                        "players": [],
                    }
                ),
                encoding="utf-8",
            )

            state.register_raw(
                sha256=sha,
                archive_path=str(replay),
                size_bytes=1,
            )
            state.mark_replay_processing(
                sha256=sha,
                status="ok",
                format_version=3,
                total_frames=600,
                duration_seconds=10.0,
                decompressed_bytes=10,
            )
            state.mark_replay_analysis(
                sha256=sha,
                analyzer_version=CURRENT_ANALYZER_VERSION,
                status="ok",
                output_path=str(output),
                sampled_state_count=100,
                player_count=0,
                raw_event_count=50,
                tick_count=600,
            )

            result = finalize_analysis_if_ready(state, derived_root=derived)
            assert result["status"] == "finalized"

            completion = json.loads(
                (
                    derived
                    / CURRENT_ANALYZER_VERSION
                    / "_complete.json"
                ).read_text(encoding="utf-8")
            )
            assert completion["raw_unique_replays"] == index
            assert completion["analysis_ok"] == index
            assert completion["analysis_ticks_reconstructed"] == index * 600


def test_finalize_refreshes_stale_training_manifest_schema(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "stale.hbr2"
    replay.write_bytes(b"x")
    sha = "e" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    output = analysis_root / f"{sha}.json"
    output.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 18000,
                "simulation": {
                    "sampleEveryTicks": 6,
                    "sampledStateCount": 3000,
                },
                "featureSummary": {"touches": 100},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=18000,
            duration_seconds=300.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(output),
            sampled_state_count=3000,
            player_count=0,
            raw_event_count=50,
            tick_count=18000,
        )

        first = finalize_analysis_if_ready(state, derived_root=derived)
        assert first["status"] == "finalized"

        manifest_path = (
            derived
            / "training"
            / f"human-imitation-{CURRENT_ANALYZER_VERSION}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema"] = "haxlab-human-imitation-manifest-v1"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        second = finalize_analysis_if_ready(state, derived_root=derived)

    assert second["status"] == "finalized"
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["schema"] == MANIFEST_SCHEMA


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_matches": 0},
        {"min_matches": -1},
        {"min_matches": 1.0},
        {"min_matches": True},
        {"min_matches": "20"},
        {"min_minutes": -0.1},
        {"min_minutes": float("nan")},
        {"min_minutes": float("inf")},
        {"min_minutes": True},
        {"min_minutes": "60"},
    ],
)
def test_finalize_rejects_malformed_thresholds_before_publication(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    derived = tmp_path / "derived"

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError):
            finalize_analysis_if_ready(
                state,
                derived_root=derived,
                **kwargs,
            )

    assert not derived.exists()


def test_finalize_rebuilds_when_completion_uses_coerced_counts(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "coerced.hbr2"
    replay.write_bytes(b"x")
    sha = "f" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    output = analysis_root / f"{sha}.json"
    output.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(output),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )

        first = finalize_analysis_if_ready(state, derived_root=derived)
        assert first["status"] == "finalized"

        completion_path = (
            derived / CURRENT_ANALYZER_VERSION / "_complete.json"
        )
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        completion["raw_unique_replays"] = "1"
        completion_path.write_text(json.dumps(completion), encoding="utf-8")

        second = finalize_analysis_if_ready(state, derived_root=derived)

    assert second["status"] == "finalized"
    refreshed = json.loads(completion_path.read_text(encoding="utf-8"))
    assert type(refreshed["raw_unique_replays"]) is int
    assert refreshed["raw_unique_replays"] == 1


def test_finalize_rejects_coerced_leaderboard_evidence_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "malformed.hbr2"
    replay.write_bytes(b"x")
    sha = "9" * 64

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(tmp_path / "unused.json"),
            sampled_state_count=100,
            player_count=1,
            raw_event_count=50,
            tick_count=600,
        )
        monkeypatch.setattr(
            finalize_module,
            "build_leaderboard",
            lambda _root: [{"matches": "20", "minutes": 60.0}],
        )

        with pytest.raises(
            ValueError,
            match=r"leaderboard row 0\.matches",
        ):
            finalize_analysis_if_ready(state, derived_root=derived)

    assert not (
        derived / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    ).exists()


def test_finalize_rebuilds_when_threshold_contract_changes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "threshold.hbr2"
    replay.write_bytes(b"x")
    sha = "8" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    output = analysis_root / f"{sha}.json"
    output.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(output),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )

        first = finalize_analysis_if_ready(
            state,
            derived_root=derived,
            min_matches=20,
            min_minutes=60.0,
        )
        second = finalize_analysis_if_ready(
            state,
            derived_root=derived,
            min_matches=1,
            min_minutes=0.0,
        )

    assert first["status"] == "finalized"
    assert second["status"] == "finalized"

    leaderboard_path = (
        derived / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    )
    leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    assert leaderboard["min_matches"] == 1
    assert leaderboard["min_minutes"] == 0.0


def test_finalize_rebuilds_corrupt_existing_leaderboard_contract(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "leaderboard-corrupt.hbr2"
    replay.write_bytes(b"x")
    sha = "7" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    output = analysis_root / f"{sha}.json"
    output.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(output),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )

        first = finalize_analysis_if_ready(state, derived_root=derived)
        assert first["status"] == "finalized"

        leaderboard_path = (
            derived / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
        )
        leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
        leaderboard["min_matches"] = "20"
        leaderboard_path.write_text(json.dumps(leaderboard), encoding="utf-8")

        second = finalize_analysis_if_ready(state, derived_root=derived)

    assert second["status"] == "finalized"
    repaired = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    assert type(repaired["min_matches"]) is int
    assert repaired["min_matches"] == 20


def test_finalize_rejects_coerced_training_manifest_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "manifest-malformed.hbr2"
    replay.write_bytes(b"x")
    sha = "6" * 64

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(tmp_path / "unused.json"),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )
        monkeypatch.setattr(finalize_module, "build_leaderboard", lambda _root: [])
        monkeypatch.setattr(
            finalize_module,
            "build_training_manifest",
            lambda **_kwargs: {
                "schema": MANIFEST_SCHEMA,
                "stats": {
                    "selected_player_count": "0",
                    "train_replay_count": 0,
                    "holdout_replay_count": 0,
                },
            },
        )

        with pytest.raises(
            ValueError,
            match=r"selected_player_count",
        ):
            finalize_analysis_if_ready(state, derived_root=derived)

    assert not (
        derived
        / "training"
        / f"human-imitation-{CURRENT_ANALYZER_VERSION}.json"
    ).exists()
    assert not (
        derived / CURRENT_ANALYZER_VERSION / "_complete.json"
    ).exists()
