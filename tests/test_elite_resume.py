from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from haxlab.learning.elite import (
    _load_training_checkpoint,
    _save_training_checkpoint,
    _training_fingerprint,
)


def _write_index(path: Path, replay: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-elite-shard-index-v1",
                "entries": [{"replay_sha256": replay}],
            }
        ),
        encoding="utf-8",
    )


def test_checkpoint_roundtrip_restores_optimizer_best_and_rng(tmp_path: Path) -> None:
    train = tmp_path / "train.json"
    validation = tmp_path / "validation.json"
    holdout = tmp_path / "holdout.json"
    _write_index(train, "a" * 64)
    _write_index(validation, "b" * 64)
    _write_index(holdout, "c" * 64)

    fingerprint, payload = _training_fingerprint(
        train_index_path=train,
        validation_index_path=validation,
        holdout_index_path=holdout,
        train_limit=None,
        validation_limit=None,
        holdout_limit=None,
        window=8,
        sequence_stride=1,
        hidden_dim=16,
        hidden_dim_2=16,
        batch_size=32,
        learning_rate=8e-4,
        l2=1e-5,
        future_horizon_steps=5,
        future_loss_weight=0.35,
        seed=1337,
    )

    params = {
        "w1": np.arange(6, dtype=np.float32).reshape(2, 3),
        "b1": np.arange(3, dtype=np.float32),
    }
    m = {key: value + 10 for key, value in params.items()}
    v = {key: value + 20 for key, value in params.items()}
    best = {key: value + 30 for key, value in params.items()}

    rng = np.random.default_rng(1337)
    _ = rng.random(5)
    pointer = _save_training_checkpoint(
        output_dir=tmp_path / "out",
        fingerprint=fingerprint,
        fingerprint_payload=payload,
        completed_epoch=2,
        step=123,
        params=params,
        m=m,
        v=v,
        best_params=best,
        best_score=0.8123,
        best_epoch=2,
        history=[{"epoch": 1}, {"epoch": 2}],
        rng=rng,
    )
    expected_next = rng.random(4)

    loaded = _load_training_checkpoint(
        output_dir=tmp_path / "out",
        fingerprint=fingerprint,
        expected_params=params,
    )
    assert loaded is not None
    assert loaded["completed_epoch"] == 2
    assert loaded["step"] == 123
    assert loaded["best_epoch"] == 2
    assert loaded["best_score"] == pytest.approx(0.8123)
    assert loaded["history"] == [{"epoch": 1}, {"epoch": 2}]
    assert pointer["completed_epoch"] == 2

    for key in params:
        np.testing.assert_array_equal(loaded["params"][key], params[key])
        np.testing.assert_array_equal(loaded["m"][key], m[key])
        np.testing.assert_array_equal(loaded["v"][key], v[key])
        np.testing.assert_array_equal(loaded["best_params"][key], best[key])

    resumed_rng = np.random.default_rng(0)
    resumed_rng.bit_generator.state = loaded["rng_state"]
    np.testing.assert_allclose(resumed_rng.random(4), expected_next)


def test_checkpoint_refuses_fingerprint_mismatch(tmp_path: Path) -> None:
    params = {"w1": np.zeros((2, 2), dtype=np.float32)}
    rng = np.random.default_rng(1)

    _save_training_checkpoint(
        output_dir=tmp_path / "out",
        fingerprint="fingerprint-a",
        fingerprint_payload={"a": 1},
        completed_epoch=1,
        step=4,
        params=params,
        m={"w1": np.zeros((2, 2), dtype=np.float32)},
        v={"w1": np.zeros((2, 2), dtype=np.float32)},
        best_params={"w1": np.ones((2, 2), dtype=np.float32)},
        best_score=0.5,
        best_epoch=1,
        history=[{"epoch": 1}],
        rng=rng,
    )

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        _load_training_checkpoint(
            output_dir=tmp_path / "out",
            fingerprint="fingerprint-b",
            expected_params=params,
        )


def test_checkpoint_refuses_tampered_arrays(tmp_path: Path) -> None:
    params = {"w1": np.zeros((2, 2), dtype=np.float32)}
    rng = np.random.default_rng(1)

    pointer = _save_training_checkpoint(
        output_dir=tmp_path / "out",
        fingerprint="fingerprint-a",
        fingerprint_payload={"a": 1},
        completed_epoch=1,
        step=4,
        params=params,
        m={"w1": np.zeros((2, 2), dtype=np.float32)},
        v={"w1": np.zeros((2, 2), dtype=np.float32)},
        best_params={"w1": np.ones((2, 2), dtype=np.float32)},
        best_score=0.5,
        best_epoch=1,
        history=[{"epoch": 1}],
        rng=rng,
    )

    Path(pointer["arrays_path"]).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="checksum mismatch"):
        _load_training_checkpoint(
            output_dir=tmp_path / "out",
            fingerprint="fingerprint-a",
            expected_params=params,
        )
