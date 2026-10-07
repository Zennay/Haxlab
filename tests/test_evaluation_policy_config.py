import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from haxlab.evaluation import policy_config as policy_config_module
from haxlab.evaluation.models import PromotionPolicy
from haxlab.evaluation.policy_config import (
    POLICY_CONFIG_SCHEMA,
    load_promotion_policy,
    load_promotion_policy_config,
    main,
)


def _write_policy(
    tmp_path: Path,
    evaluation_lines: str,
    *,
    filename: str = "autonomy.toml",
) -> Path:
    path = tmp_path / filename
    path.write_text(f"[evaluation]\n{evaluation_lines}", encoding="utf-8")
    return path


def test_canonical_autonomy_config_matches_runtime_policy_defaults() -> None:
    policy = load_promotion_policy(Path("configs/autonomy.toml"))

    assert policy == PromotionPolicy()


def test_policy_config_provenance_binds_exact_source_bytes() -> None:
    path = Path("configs/autonomy.toml")
    raw = path.read_bytes()

    loaded = load_promotion_policy_config(path)

    assert loaded.schema == POLICY_CONFIG_SCHEMA
    assert loaded.policy == PromotionPolicy()
    assert loaded.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert loaded.source_size_bytes == len(raw)


def test_semantically_equal_policy_records_byte_drift(tmp_path: Path) -> None:
    lines = (
        "minimum_games_vs_champion = 500\n"
        "minimum_score_rate_lower_bound = 0.51\n"
        "minimum_frozen_scenario_pass_rate = 0.98\n"
    )
    first = _write_policy(tmp_path, lines, filename="first.toml")
    second = _write_policy(
        tmp_path,
        lines + "# same policy, different source bytes\n",
        filename="second.toml",
    )

    first_loaded = load_promotion_policy_config(first)
    second_loaded = load_promotion_policy_config(second)

    assert first_loaded.schema == second_loaded.schema == POLICY_CONFIG_SCHEMA
    assert first_loaded.policy == second_loaded.policy == PromotionPolicy()
    assert first_loaded.source_sha256 != second_loaded.source_sha256
    assert first_loaded.source_size_bytes != second_loaded.source_size_bytes


def test_policy_config_maps_external_names_to_runtime_policy(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        "minimum_games_vs_champion = 750\n"
        "minimum_score_rate_lower_bound = 0.53\n"
        "minimum_frozen_scenario_pass_rate = 0.99\n",
    )

    assert load_promotion_policy(path) == PromotionPolicy(
        minimum_games=750,
        minimum_score_rate_lower_bound=0.53,
        minimum_scenario_pass_rate=0.99,
        allow_critical_regressions=False,
    )


def test_policy_config_fails_closed_on_runtime_policy_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True)
    class ExpandedPromotionPolicy:
        minimum_games: int = 500
        minimum_score_rate_lower_bound: float = 0.51
        minimum_scenario_pass_rate: float = 0.98
        allow_critical_regressions: bool = False
        new_unbound_threshold: float = 0.25

    path = _write_policy(
        tmp_path,
        "minimum_games_vs_champion = 500\n"
        "minimum_score_rate_lower_bound = 0.51\n"
        "minimum_frozen_scenario_pass_rate = 0.98\n",
    )
    monkeypatch.setattr(
        policy_config_module,
        "PromotionPolicy",
        ExpandedPromotionPolicy,
    )

    with pytest.raises(ValueError, match="runtime schema mismatch"):
        policy_config_module.load_promotion_policy_config(path)


def test_policy_config_cli_emits_versioned_policy_and_source_provenance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = Path("configs/autonomy.toml")
    raw = path.read_bytes()

    assert main([str(path)]) == 0

    output = capsys.readouterr().out
    payload = {
        "schema": POLICY_CONFIG_SCHEMA,
        "policy": {
            "allow_critical_regressions": False,
            "minimum_games": 500,
            "minimum_scenario_pass_rate": 0.98,
            "minimum_score_rate_lower_bound": 0.51,
        },
        "source": {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
    }
    assert json.loads(output) == payload
    assert output == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def test_policy_config_cli_fails_closed_on_invalid_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_policy(
        tmp_path,
        "minimum_games_vs_champion = 0\n"
        "minimum_score_rate_lower_bound = 0.51\n"
        "minimum_frozen_scenario_pass_rate = 0.98\n",
    )

    with pytest.raises(SystemExit) as exc_info:
        main([str(path)])

    assert exc_info.value.code == 2
    assert "minimum_games_vs_champion must be >= 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "evaluation_lines",
    [
        (
            "minimum_games_vs_champion = 500\n"
            "minimum_score_rate_lower_bound = 0.51\n"
        ),
        (
            "minimum_games_vs_champion = 500\n"
            "minimum_score_rate_lower_bound = 0.51\n"
            "minimum_frozen_scenario_pass_rate = 0.98\n"
            "allow_critical_regressions = true\n"
        ),
    ],
)
def test_policy_config_requires_exact_evaluation_keyset(
    tmp_path: Path,
    evaluation_lines: str,
) -> None:
    path = _write_policy(tmp_path, evaluation_lines)

    with pytest.raises(ValueError, match="keyset mismatch"):
        load_promotion_policy(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("minimum_games_vs_champion", "true"),
        ("minimum_games_vs_champion", "0"),
        ("minimum_games_vs_champion", "500.0"),
        ("minimum_score_rate_lower_bound", '"0.51"'),
        ("minimum_score_rate_lower_bound", "true"),
        ("minimum_score_rate_lower_bound", "1.01"),
        ("minimum_frozen_scenario_pass_rate", "-0.01"),
        ("minimum_frozen_scenario_pass_rate", "nan"),
    ],
)
def test_policy_config_rejects_coercible_or_out_of_range_values(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    values = {
        "minimum_games_vs_champion": "500",
        "minimum_score_rate_lower_bound": "0.51",
        "minimum_frozen_scenario_pass_rate": "0.98",
    }
    values[key] = value
    path = _write_policy(
        tmp_path,
        "".join(f"{name} = {raw}\n" for name, raw in values.items()),
    )

    with pytest.raises(ValueError):
        load_promotion_policy(path)


def test_policy_config_rejects_duplicate_toml_keys(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        "minimum_games_vs_champion = 500\n"
        "minimum_games_vs_champion = 750\n"
        "minimum_score_rate_lower_bound = 0.51\n"
        "minimum_frozen_scenario_pass_rate = 0.98\n",
    )

    with pytest.raises(ValueError, match="invalid TOML"):
        load_promotion_policy(path)


def test_policy_config_rejects_missing_evaluation_table(tmp_path: Path) -> None:
    path = tmp_path / "autonomy.toml"
    path.write_text("[training]\nautomatic_training_enabled = true\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"requires an \[evaluation\] table"):
        load_promotion_policy(path)


def test_policy_config_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "autonomy.toml"
    path.write_text("[evaluation\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid TOML"):
        load_promotion_policy(path)


def test_policy_config_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "autonomy.toml"
    path.write_bytes(b"[evaluation]\nminimum_games_vs_champion = 500\n\xff")

    with pytest.raises(ValueError, match="invalid TOML"):
        load_promotion_policy(path)


def test_policy_config_rejects_symlink(tmp_path: Path) -> None:
    target = _write_policy(
        tmp_path,
        "minimum_games_vs_champion = 500\n"
        "minimum_score_rate_lower_bound = 0.51\n"
        "minimum_frozen_scenario_pass_rate = 0.98\n",
    )
    link = tmp_path / "policy-link.toml"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="non-symlink"):
        load_promotion_policy(link)


def test_policy_config_rejects_non_path_input() -> None:
    with pytest.raises(ValueError, match="pathlib.Path"):
        load_promotion_policy("configs/autonomy.toml")  # type: ignore[arg-type]
