from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path("configs/evaluation/multisource-suite-v2.json")
WORKFLOW_PATH = Path(".github/workflows/multisource-suite-v2.yml")


def test_multisource_production_config_has_exact_frozen_contract() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["schema"] == "haxlab-multisource-evaluation-config-v2"
    assert type(config["source_count"]) is int
    assert config["source_count"] > 0
    assert type(config["minimum_sources"]) is int
    assert config["minimum_sources"] == config["source_count"]
    assert config["mirror_sides"] is True
    assert config["raw_policy_only"] is True
    assert (
        config["selection_algorithm"]
        == "state-pass-v4-sampled-states-desc-sha256-asc"
    )


def test_production_workflow_does_not_coerce_source_count() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'wanted = config["source_count"]' in workflow
    assert 'wanted = int(config["source_count"])' not in workflow
    assert "source_count must be a native positive integer" in workflow
    assert "minimum_sources/source_count mismatch" in workflow
    assert '"raw_policy_only": True' in workflow
    assert '"mirror_sides": True' in workflow
