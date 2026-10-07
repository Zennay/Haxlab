from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.evaluation.multisource_suite import (
    MULTISOURCE_SUITE_SCHEMA,
    build_frozen_multisource_suite,
    canonical_sha256,
    source_seed,
)


def _source(root: Path, replay_char: str, *, scenarios: int = 4) -> Path:
    root.mkdir(parents=True)
    replay_sha = replay_char * 64
    (root / "source.json").write_text(
        json.dumps(
            {
                "schema": "haxlab-replay-scenario-source-v1",
                "sha256": replay_sha,
                "raw_path": f"/raw/{replay_sha}.hbr2",
                "analysis_path": f"/analysis/{replay_sha}.json",
                "duration_seconds": 300.0,
                "sampled_states": 2400,
                "touches": 180,
                "role_confidence_min": 0.81,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "stadium.hbs").write_text(
        '{"name":"Arena v2 test"}\n',
        encoding="utf-8",
    )
    rows = [
        {
            "frame": 100 + index * 60,
            "ball": {"x": index, "y": 0},
            "history": [{"frame": 94 + index * 60, "features": {}}],
        }
        for index in range(scenarios)
    ]
    (root / "scenarios.json").write_text(
        json.dumps(
            {
                "schema": "haxlab-replay-seeded-scenarios-v1",
                "scenario_count": len(rows),
                "scenarios": rows,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_frozen_multisource_suite_is_order_independent_and_reproducible(
    tmp_path: Path,
) -> None:
    roots = [
        _source(tmp_path / "c", "c"),
        _source(tmp_path / "a", "a"),
        _source(tmp_path / "b", "b"),
    ]

    first = build_frozen_multisource_suite(
        roots,
        suite_seed=20261001,
        scenarios_per_source=4,
    )
    second = build_frozen_multisource_suite(
        list(reversed(roots)),
        suite_seed=20261001,
        scenarios_per_source=4,
    )

    assert first == second
    assert first["schema"] == MULTISOURCE_SUITE_SCHEMA
    assert first["frozen"] is True
    assert first["source_count"] == 3
    assert first["replay_sha256s"] == ["a" * 64, "b" * 64, "c" * 64]
    assert first["total_scenarios"] == 12
    assert first["total_mirrored_matches"] == 24
    assert first["suite_sha256"] == canonical_sha256(
        {key: value for key, value in first.items() if key != "suite_sha256"}
    )
    assert len({source["arena_seed"] for source in first["sources"]}) == 3
    assert all(
        set(source["files_sha256"]) == {
            "source.json",
            "stadium.hbs",
            "scenarios.json",
        }
        for source in first["sources"]
    )


def test_source_seed_is_stable_and_source_specific() -> None:
    first = source_seed(1337, "a" * 64, 1)
    assert first == source_seed(1337, "a" * 64, 1)
    assert first != source_seed(1337, "b" * 64, 2)
    assert 0 <= first <= 0x7FFFFFFF


def test_frozen_suite_rejects_duplicate_replay_sources(tmp_path: Path) -> None:
    roots = [
        _source(tmp_path / "one", "a"),
        _source(tmp_path / "two", "a"),
        _source(tmp_path / "three", "b"),
    ]

    with pytest.raises(ValueError, match="duplicate replay SHA-256"):
        build_frozen_multisource_suite(
            roots,
            scenarios_per_source=4,
        )


def test_frozen_suite_rejects_underfilled_source(tmp_path: Path) -> None:
    roots = [
        _source(tmp_path / "a", "a", scenarios=4),
        _source(tmp_path / "b", "b", scenarios=3),
        _source(tmp_path / "c", "c", scenarios=4),
    ]

    with pytest.raises(ValueError, match="3 scenarios; 4 required"):
        build_frozen_multisource_suite(
            roots,
            scenarios_per_source=4,
        )


def test_frozen_suite_requires_at_least_three_sources(tmp_path: Path) -> None:
    roots = [
        _source(tmp_path / "a", "a"),
        _source(tmp_path / "b", "b"),
    ]

    with pytest.raises(ValueError, match="requires at least 3 sources"):
        build_frozen_multisource_suite(
            roots,
            scenarios_per_source=4,
        )


def test_frozen_suite_rejects_non_object_source_descriptor(tmp_path: Path) -> None:
    root = _source(tmp_path / "a", "a")
    (root / "source.json").write_text(
        json.dumps(["not", "an", "object"]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source.json must contain a JSON object"):
        build_frozen_multisource_suite(
            [root, _source(tmp_path / "b", "b"), _source(tmp_path / "c", "c")],
            scenarios_per_source=4,
        )


def test_frozen_suite_rejects_non_object_scenario_descriptor(tmp_path: Path) -> None:
    root = _source(tmp_path / "a", "a")
    (root / "scenarios.json").write_text(
        json.dumps(["not", "an", "object"]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scenarios.json must contain a JSON object"):
        build_frozen_multisource_suite(
            [root, _source(tmp_path / "b", "b"), _source(tmp_path / "c", "c")],
            scenarios_per_source=4,
        )


def test_frozen_suite_rejects_non_array_scenarios(tmp_path: Path) -> None:
    root = _source(tmp_path / "a", "a")
    payload = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    payload["scenarios"] = {"unexpected": "mapping"}
    (root / "scenarios.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scenarios must be a JSON array"):
        build_frozen_multisource_suite(
            [root, _source(tmp_path / "b", "b"), _source(tmp_path / "c", "c")],
            scenarios_per_source=4,
        )


def test_frozen_suite_rejects_non_object_scenario_row(tmp_path: Path) -> None:
    root = _source(tmp_path / "a", "a")
    payload = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    payload["scenarios"][0] = "malformed"
    (root / "scenarios.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="every scenario must be a JSON object"):
        build_frozen_multisource_suite(
            [root, _source(tmp_path / "b", "b"), _source(tmp_path / "c", "c")],
            scenarios_per_source=4,
        )


@pytest.mark.parametrize("declared_count", [True, 4.0, "4"])
def test_frozen_suite_requires_strict_integer_scenario_count(
    tmp_path: Path,
    declared_count: object,
) -> None:
    root = _source(tmp_path / "a", "a")
    payload = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    payload["scenario_count"] = declared_count
    (root / "scenarios.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scenario_count must be an integer"):
        build_frozen_multisource_suite(
            [root, _source(tmp_path / "b", "b"), _source(tmp_path / "c", "c")],
            scenarios_per_source=4,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("suite_seed", "1337"),
        ("suite_seed", 1337.0),
        ("suite_seed", True),
        ("scenarios_per_source", "4"),
        ("scenarios_per_source", 4.0),
        ("scenarios_per_source", True),
        ("rollout_seconds", "30"),
        ("sample_every_ticks", 6.0),
        ("history_window", False),
        ("minimum_sources", "3"),
    ],
)
def test_frozen_suite_rejects_coerced_config_integer_types(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    roots = [
        _source(tmp_path / "a", "a"),
        _source(tmp_path / "b", "b"),
        _source(tmp_path / "c", "c"),
    ]
    kwargs = {
        "suite_seed": 1337,
        "scenarios_per_source": 4,
        "rollout_seconds": 30,
        "sample_every_ticks": 6,
        "history_window": 8,
        "minimum_sources": 3,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=f"{field} must be a native integer"):
        build_frozen_multisource_suite(roots, **kwargs)


@pytest.mark.parametrize(
    ("field", "value", "minimum"),
    [
        ("scenarios_per_source", 0, 1),
        ("rollout_seconds", 4, 5),
        ("sample_every_ticks", 0, 1),
        ("history_window", 1, 2),
        ("minimum_sources", 0, 1),
    ],
)
def test_frozen_suite_rejects_out_of_range_config(
    tmp_path: Path,
    field: str,
    value: int,
    minimum: int,
) -> None:
    roots = [
        _source(tmp_path / "a", "a"),
        _source(tmp_path / "b", "b"),
        _source(tmp_path / "c", "c"),
    ]
    kwargs = {
        "suite_seed": 1337,
        "scenarios_per_source": 4,
        "rollout_seconds": 30,
        "sample_every_ticks": 6,
        "history_window": 8,
        "minimum_sources": 3,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=f"{field} must be >= {minimum}"):
        build_frozen_multisource_suite(roots, **kwargs)


def test_source_seed_rejects_coerced_inputs() -> None:
    with pytest.raises(ValueError, match="suite_seed must be a native integer"):
        source_seed("1337", "a" * 64, 1)
    with pytest.raises(ValueError, match="source_index must be a native integer"):
        source_seed(1337, "a" * 64, 1.0)
    with pytest.raises(ValueError, match="source_index must be >= 1"):
        source_seed(1337, "a" * 64, 0)
