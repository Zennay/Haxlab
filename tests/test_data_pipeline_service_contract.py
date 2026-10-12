from __future__ import annotations

import ast
import math
import re
import shlex
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

PIPELINE_SERVICES = {
    "ingest": DEPLOY / "haxlab-ingest.service",
    "worker": DEPLOY / "haxlab-worker.service",
    "analyzer": DEPLOY / "haxlab-analyzer.service",
}

EXPECTED_MODULES = {
    "ingest": "haxlab.runtime.daemon",
    "worker": "haxlab.runtime.worker",
    "analyzer": "haxlab.runtime.analyzer",
}

EXPECTED_SCRIPTS = {
    "haxlab-daemon": "haxlab.runtime.daemon:main",
    "haxlab-worker": "haxlab.runtime.worker:main",
    "haxlab-analyzer": "haxlab.runtime.analyzer:main",
}

MODULE_PATHS = {
    name: ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
    for name, module in EXPECTED_MODULES.items()
}


def _service_text(name: str) -> str:
    return PIPELINE_SERVICES[name].read_text(encoding="utf-8")


def _logical_service_text(name: str) -> str:
    return re.sub(r"\\\n\s*", " ", _service_text(name))


def _service_argv_from_text(unit_text: str) -> list[str]:
    """Read precisely one logical ExecStart command from a unit."""
    logical_text = re.sub(r"\\\n\s*", " ", unit_text)
    commands = re.findall(r"^ExecStart=(.+)$", logical_text, flags=re.MULTILINE)
    assert len(commands) == 1, f"expected single ExecStart: found {len(commands)}"
    return shlex.split(commands[0])


def _service_argv(name: str) -> list[str]:
    return _service_argv_from_text(_service_text(name))


def _argument_from_argv(argv: list[str], flag: str) -> str:
    for index, token in enumerate(argv):
        if token.startswith(flag + "="):
            return token[len(flag) + 1:]
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
    raise AssertionError(f"missing service option {flag}")


def _argument(name: str, flag: str) -> str:
    return _argument_from_argv(_service_argv(name), flag)


def _service_flags_from_argv(argv: list[str]) -> set[str]:
    return {
        token.partition("=")[0]
        for token in argv
        if re.fullmatch(r"--[a-z0-9-]+(?:=.*)?", token)
    }


def _service_flags(name: str) -> set[str]:
    return _service_flags_from_argv(_service_argv(name))


def _declared_flags(name: str) -> set[str]:
    tree = ast.parse(MODULE_PATHS[name].read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                if argument.value.startswith("--"):
                    flags.add(argument.value)
    return flags


def test_pipeline_services_keep_required_dependency_order() -> None:
    ingest = _service_text("ingest")
    worker = _service_text("worker")
    analyzer = _service_text("analyzer")

    assert "WantedBy=multi-user.target" in ingest
    assert "Requires=haxlab-ingest.service" in worker
    assert "After=network.target haxlab-ingest.service" in worker
    assert "Requires=haxlab-worker.service" in analyzer
    assert "After=network.target haxlab-worker.service" in analyzer


def test_pipeline_services_use_expected_runtime_modules_and_identity() -> None:
    for name, module in EXPECTED_MODULES.items():
        text = _logical_service_text(name)
        assert "User=haxlab" in text
        assert "Group=haxlab" in text
        assert "WorkingDirectory=/opt/haxlab" in text
        assert f"ExecStart=/opt/haxlab/.venv/bin/python -m {module}" in text
        assert "Restart=always" in text
        assert "RestartSec=5" in text
        assert "NoNewPrivileges=true" in text
        assert "ProtectSystem=strict" in text
        assert "ProtectHome=true" in text
        assert "ReadWritePaths=/var/lib/haxlab" in text


def test_pipeline_services_share_one_canonical_state_database() -> None:
    state_databases = {
        name: _argument(name, "--state-db")
        for name in PIPELINE_SERVICES
    }

    assert set(state_databases.values()) == {
        "/var/lib/haxlab/state/haxlab.sqlite3"
    }


def test_pipeline_service_paths_match_canonical_data_layout() -> None:
    assert _argument("ingest", "--incoming") == "/var/lib/haxlab/incoming"
    assert _argument("ingest", "--raw") == "/var/lib/haxlab/raw/replays"
    assert _argument("analyzer", "--derived-root") == "/var/lib/haxlab/derived"
    assert _argument("analyzer", "--decoder-script") == (
        "/opt/haxlab/tools/decode_replay.js"
    )


def test_packaged_pipeline_entrypoints_match_systemd_modules() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    for script, target in EXPECTED_SCRIPTS.items():
        assert scripts.get(script) == target


def test_systemd_flags_are_declared_by_the_bound_runtime_modules() -> None:
    for name in PIPELINE_SERVICES:
        service_flags = _service_flags(name)
        declared_flags = _declared_flags(name)
        assert service_flags <= declared_flags, (
            f"{PIPELINE_SERVICES[name].name} passes undeclared flags: "
            f"{sorted(service_flags - declared_flags)}"
        )


def _installed_cli_links(script_name: str) -> set[str]:
    text = (DEPLOY / script_name).read_text(encoding="utf-8")
    return set(
        re.findall(
            r'ln -sf "\$\{APP_DIR\}/\.venv/bin/(haxlab[^"]*)" '
            r'/usr/local/bin/[^\s]+',
            text,
        )
    )


def test_fresh_install_and_update_publish_the_same_haxlab_clis() -> None:
    fresh_install = _installed_cli_links("install-vps.sh")
    update_install = _installed_cli_links("update-vps.sh")

    assert fresh_install == update_install
    assert "haxlab-generation-loop" in fresh_install


def _installed_units(script_name: str) -> set[str]:
    text = (DEPLOY / script_name).read_text(encoding="utf-8")
    return set(
        re.findall(
            r'install -m 0644 "\$\{APP_DIR\}/deploy/(haxlab-[^"]+\.(?:service|timer))" ',
            text,
        )
    )


def test_deployed_cli_links_are_backed_by_pyproject_entrypoints() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = set(pyproject["project"]["scripts"])
    deployed = _installed_cli_links("install-vps.sh")

    assert deployed <= scripts


def test_fresh_install_and_update_copy_the_same_systemd_units() -> None:
    fresh_units = _installed_units("install-vps.sh")
    update_units = _installed_units("update-vps.sh")

    assert fresh_units == update_units
    assert {
        "haxlab-ingest.service",
        "haxlab-worker.service",
        "haxlab-analyzer.service",
        "haxlab-autonomy.service",
        "haxlab-autonomy.timer",
    } <= fresh_units


def _is_positive_finite_service_number(raw: str) -> bool:
    """Reject malformed and non-finite systemd numeric CLI arguments."""
    try:
        value = float(raw)
    except (ValueError, OverflowError):
        return False
    return math.isfinite(value) and value > 0


def test_pipeline_service_runtime_values_are_strictly_positive() -> None:
    numeric_arguments = {
        "ingest": ("--interval", "--minimum-file-age"),
        "worker": ("--batch-size", "--interval"),
        "analyzer": (
            "--workers",
            "--batch-size",
            "--sample-every-ticks",
            "--timeout-seconds",
            "--interval",
        ),
    }

    for service, flags in numeric_arguments.items():
        for flag in flags:
            raw = _argument(service, flag)
            assert _is_positive_finite_service_number(raw), (
                f"{service} has non-positive/non-finite {flag}: {raw!r}"
            )


def test_service_numeric_validator_accepts_finite_positive_values() -> None:
    for raw in ("1", "6", "0.125", "1e3", "1e-3"):
        assert _is_positive_finite_service_number(raw)


def test_service_numeric_validator_rejects_nonfinite_or_invalid_values() -> None:
    for raw in (
        "inf", "+inf", "-inf", "Infinity", "1e9999",
        "nan", "NaN", "0", "-1", "-0.5", "broken", "",
    ):
        assert not _is_positive_finite_service_number(raw), raw


def test_service_parser_recognizes_equals_and_space_option_forms() -> None:
    argv = _service_argv_from_text(
        "[Service]\nExecStart=python -m worker --interval=5 --batch-size 24\n"
    )
    assert _service_flags_from_argv(argv) == {"--interval", "--batch-size"}
    assert _argument_from_argv(argv, "--interval") == "5"
    assert _argument_from_argv(argv, "--batch-size") == "24"


def test_service_parser_exposes_undeclared_equals_options() -> None:
    argv = _service_argv_from_text(
        "[Service]\nExecStart=python -m worker --interval 5 --unknown-opt=1\n"
    )
    assert "--unknown-opt" in _service_flags_from_argv(argv)


def test_service_parser_rejects_partial_option_matches() -> None:
    argv = _service_argv_from_text(
        "[Service]\nExecStart=python -m worker --interval-extra=5\n"
    )
    assert _service_flags_from_argv(argv) == {"--interval-extra"}
    try:
        _argument_from_argv(argv, "--interval")
    except AssertionError as error:
        assert "missing service option" in str(error)
    else:
        raise AssertionError("partial prefix must not be treated as a declared flag")


def test_service_parser_preserves_shell_line_continuations() -> None:
    newline = chr(92) + "\n"
    unit = (
        "[Service]\nExecStart=python -m worker " + newline
        + "  --interval=5 " + newline
        + "  --batch-size 24\n"
    )
    argv = _service_argv_from_text(unit)
    assert _service_flags_from_argv(argv) == {"--interval", "--batch-size"}

def test_service_parser_rejects_multiple_execstarts() -> None:
    try:
        _service_argv_from_text(
            "[Service]\nExecStart=python --interval 5\n"
            "ExecStart=python --interval 6\n"
        )
    except AssertionError as error:
        assert "expected single ExecStart" in str(error)
    else:
        raise AssertionError("multiple systemd ExecStart lines are ambiguous")
