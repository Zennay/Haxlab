from __future__ import annotations

import inspect
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
SKILL_AUDIT_ROOT = ROOT / "src" / "haxlab" / "skill"

POLICIES = (
    ("atexit_state", "test_data_pipeline_audit_atexit_state_contract.py", "scan_source"),
    ("background_execution", "test_data_pipeline_audit_background_execution_contract.py", "scan_source"),
    ("ambient_determinism", "test_data_pipeline_audit_determinism_contract.py", "_ambient_violations"),
    ("exception_boundary", "test_data_pipeline_audit_exception_boundary.py", "_violations"),
    ("module_global_state", "test_data_pipeline_audit_global_state_contract.py", "scan_source"),
    ("hermeticity", "test_data_pipeline_audit_hermeticity.py", "_forbidden_effects"),\n    ("gc_state", "test_data_pipeline_audit_gc_state_contract.py", "scan_source"),
    ("instrumentation_state", "test_data_pipeline_audit_instrumentation_state_contract.py", "scan_source"),
    ("interpreter_tuning", "test_data_pipeline_audit_interpreter_tuning_contract.py", "scan_source"),
    ("layer_boundary", "test_data_pipeline_audit_layer_boundary.py", "scan_source"),
    ("liveness", "test_data_pipeline_audit_liveness_contract.py", "scan_source"),
    ("logging_state", "test_data_pipeline_audit_logging_state_contract.py", "_logging_state_violations"),
    ("mmap_readonly", "test_data_pipeline_audit_mmap_readonly_contract.py", "scan_source"),
    ("mutable_defaults", "test_data_pipeline_audit_mutable_defaults_contract.py", "scan_source"),
    ("native_ffi", "test_data_pipeline_audit_native_ffi_contract.py", "scan_source"),
    ("process_termination", "test_data_pipeline_audit_process_termination_contract.py", "scan_source"),
    ("readonly_io", "test_data_pipeline_audit_readonly_contract.py", "_mutation_violations"),
    ("runtime_output_purity", "test_data_pipeline_audit_runtime_output_purity_contract.py", "scan_source"),
    ("safe_deserialization", "test_data_pipeline_audit_safe_deserialization.py", "_deserialization_violations"),
    ("sqlite_extension", "test_data_pipeline_audit_sqlite_extension_contract.py", "scan_source"),
    ("warning_state", "test_data_pipeline_audit_warning_state_contract.py", "scan_source"),
)


SMOKE_SOURCES = {
    "atexit_state": "import atexit\natexit.register(lambda: None)\n",
    "background_execution": (
        "import threading\n"
        "def audit():\n"
        "    return threading.Thread(target=lambda: None)\n"
    ),
    "ambient_determinism": "import random\nvalue = random.random()\n",
    "exception_boundary": (
        "def audit():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n"
    ),
    "module_global_state": (
        "CACHE = []\n"
        "def audit():\n"
        "    CACHE.append('x')\n"
    ),
    "hermeticity": "import socket\nsocket.socket()\n",
    "instrumentation_state": "import sys\nsys.addaudithook(lambda *args: None)\n",
    "interpreter_tuning": "import sys\nsys.setrecursionlimit(2000)\n",
    "layer_boundary": "import haxlab.evaluation.promotion as promotion\n",
    "liveness": "def audit():\n    input('continue?')\n",
    "logging_state": "import logging\nlogging.basicConfig(level=10)\n",
    "mmap_readonly": (
        "import mmap\n"
        "def audit(fd):\n"
        "    return mmap.mmap(fd, 4096, access=mmap.ACCESS_WRITE)\n"
    ),
    "mutable_defaults": "def audit(cache=[]):\n    return cache\n",
    "native_ffi": "import ctypes\n",
    "process_termination": "import os\ndef audit():\n    os._exit(1)\n",
    "readonly_io": "def audit(path):\n    open(path, 'w').write('x')\n",
    "runtime_output_purity": "def audit():\n    print('noise')\n",
    "safe_deserialization": (
        "import pickle\n"
        "def audit(payload):\n"
        "    return pickle.loads(payload)\n"
    ),
    "sqlite_extension": (
        "def audit(connection):\n"
        "    connection.load_extension('/tmp/ext.so')\n"
    ),
    "warning_state": "import warnings\nwarnings.filterwarnings('ignore')\n",
}


def _skill_audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for path in SKILL_AUDIT_ROOT.rglob("*_audit.py")
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def _policy_scanner(test_file: str, scanner_name: str):
    namespace = runpy.run_path(str(ROOT / "tests" / test_file))
    scanner = namespace.get(scanner_name)
    assert callable(scanner), f"{test_file} does not expose callable {scanner_name}"
    return scanner


def _scan(scanner, source: str, *, filename: str):
    kwargs: dict[str, str] = {}
    parameters = inspect.signature(scanner).parameters
    if "filename" in parameters:
        kwargs["filename"] = filename
    if "module_name" in parameters:
        relative_module = Path(filename).with_suffix("")
        assert relative_module.parts[0] == "src"
        kwargs["module_name"] = ".".join(relative_module.parts[1:])
    result = scanner(source, **kwargs)
    assert isinstance(result, list), (
        f"policy scanner {scanner.__name__} returned {type(result).__name__}, expected list"
    )
    return result


def test_skill_auditors_pass_integrated_core_audit_policies() -> None:
    paths = _skill_audit_paths()
    assert paths, "expected at least one skill *_audit.py module"
    assert SKILL_AUDIT_ROOT / "leaderboard_audit.py" in paths

    scanners = [
        (name, _policy_scanner(test_file, scanner_name))
        for name, test_file, scanner_name in POLICIES
    ]

    failures: dict[str, dict[str, list[str]]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        for policy_name, scanner in scanners:
            findings = _scan(scanner, source, filename=relative)
            if findings:
                failures.setdefault(relative, {})[policy_name] = findings

    assert failures == {}


def test_bridge_targets_live_policy_implementations_instead_of_copying_rules() -> None:
    for _, test_file, scanner_name in POLICIES:
        policy_path = ROOT / "tests" / test_file
        assert policy_path.is_file()
        namespace = runpy.run_path(str(policy_path))
        assert callable(namespace.get(scanner_name))


def test_policy_manifest_covers_every_integrated_core_audit_policy() -> None:
    discovered = {
        path.name
        for path in (ROOT / "tests").glob("test_data_pipeline_audit_*.py")
        if path.is_file()
    }
    bridged = {test_file for _, test_file, _ in POLICIES}

    assert discovered == bridged


def test_each_bridged_policy_scanner_is_live() -> None:
    assert set(SMOKE_SOURCES) == {name for name, _, _ in POLICIES}

    filename = "src/haxlab/skill/synthetic_audit.py"
    for policy_name, test_file, scanner_name in POLICIES:
        scanner = _policy_scanner(test_file, scanner_name)
        findings = _scan(
            scanner,
            SMOKE_SOURCES[policy_name],
            filename=filename,
        )
        assert findings, f"{policy_name} scanner did not detect its smoke violation"
