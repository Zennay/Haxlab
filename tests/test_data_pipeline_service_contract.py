from __future__ import annotations

import ast
import re
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


def _argument(name: str, flag: str) -> str:
    match = re.search(
        rf"(?:^|\s){re.escape(flag)}\s+(\S+)",
        _logical_service_text(name),
    )
    assert match is not None, f"{PIPELINE_SERVICES[name].name} is missing {flag}"
    return match.group(1)


def _service_flags(name: str) -> set[str]:
    match = re.search(
        r"^ExecStart=(.+)$",
        _logical_service_text(name),
        flags=re.MULTILINE,
    )
    assert match is not None, f"{PIPELINE_SERVICES[name].name} is missing ExecStart"
    return set(re.findall(r"(?<!\S)--[a-z0-9-]+(?=\s|$)", match.group(1)))


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
            value = float(_argument(service, flag))
            assert value > 0, f"{service} has non-positive {flag}: {value}"
