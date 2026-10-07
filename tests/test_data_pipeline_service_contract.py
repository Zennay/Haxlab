from __future__ import annotations

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
