import json
from pathlib import Path

import pytest

from haxlab.evaluation.models import PromotionPolicy
from haxlab.evaluation.policy_config import load_promotion_policy, main


def _write_policy(tmp_path: Path, evaluation_lines: str) -> Path:
    path = tmp_path / "autonomy.toml"
    path.write_text(f"[evaluation]\n{evaluation_lines}", encoding="utf-8")
    return path


def test_canonical_autonomy_config_matches_runtime_policy_defaults() -> None:
    policy = load_promotion_policy(Path("configs/autonomy.toml"))

    assert policy == PromotionPolicy()


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


def test_policy_config_cli_emits_machine_readable_runtime_policy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["configs/autonomy.toml"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "allow_critical_regressions": False,
        "minimum_games": 500,
        "minimum_scenario_pass_rate": 0.98,
        "minimum_score_rate_lower_bound": 0.51,
    }


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
