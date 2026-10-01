from __future__ import annotations

import numpy as np

from haxlab.learning.elite import _apply_sequence_state_jitter


COLUMNS = [
    "own_x",
    "own_y",
    "own_vx",
    "own_vy",
    "ball_dx",
    "ball_dy",
    "tm1_dx",
    "tm1_present",
    "op1_dx",
    "op1_present",
    "score_diff",
]


def _sequence() -> np.ndarray:
    base = np.arange(4 * 3 * len(COLUMNS), dtype=np.float32)
    return base.reshape(4, 3, len(COLUMNS)) / 10.0


def test_state_jitter_is_train_role_scoped_and_preserves_discrete_features() -> None:
    sequence = _sequence()
    roles = np.asarray([0, 1, 2, 3], dtype=np.int64)

    result = _apply_sequence_state_jitter(
        sequence,
        roles,
        input_columns=COLUMNS,
        rng=np.random.default_rng(123),
        jitter_std=0.10,
    )

    np.testing.assert_array_equal(result[0], sequence[0])
    np.testing.assert_array_equal(result[1], sequence[1])

    for row in (2, 3):
        assert not np.array_equal(result[row], sequence[row])
        for name in ("tm1_present", "op1_present", "score_diff"):
            idx = COLUMNS.index(name)
            np.testing.assert_array_equal(
                result[row, :, idx],
                sequence[row, :, idx],
            )


def test_state_jitter_is_sequence_consistent_per_feature() -> None:
    sequence = _sequence()
    roles = np.asarray([2, 3, 2, 3], dtype=np.int64)

    result = _apply_sequence_state_jitter(
        sequence,
        roles,
        input_columns=COLUMNS,
        rng=np.random.default_rng(456),
        jitter_std=0.10,
    )

    for row in range(sequence.shape[0]):
        delta = result[row] - sequence[row]
        for name in ("own_x", "ball_dx", "tm1_dx", "op1_dx"):
            idx = COLUMNS.index(name)
            assert float(np.ptp(delta[:, idx])) <= 5e-7


def test_state_jitter_is_deterministic_for_seed_and_disabled_is_noop() -> None:
    sequence = _sequence()
    roles = np.asarray([0, 1, 2, 3], dtype=np.int64)

    first = _apply_sequence_state_jitter(
        sequence,
        roles,
        input_columns=COLUMNS,
        rng=np.random.default_rng(789),
        jitter_std=0.10,
    )
    second = _apply_sequence_state_jitter(
        sequence,
        roles,
        input_columns=COLUMNS,
        rng=np.random.default_rng(789),
        jitter_std=0.10,
    )
    np.testing.assert_array_equal(first, second)

    disabled = _apply_sequence_state_jitter(
        sequence,
        roles,
        input_columns=COLUMNS,
        rng=np.random.default_rng(789),
        jitter_std=0.0,
    )
    assert disabled is sequence
