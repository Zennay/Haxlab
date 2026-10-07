from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.ingestion.failure_provenance import canonical_failure_source


def test_failure_source_is_stable_across_absolute_export_roots() -> None:
    root_a = Path("/srv/haxlab-a/raw")
    root_b = Path("/mnt/other-machine/raw")

    source_a = canonical_failure_source(
        root_a,
        root_a / "discord" / "channel.json",
    )
    source_b = canonical_failure_source(
        root_b,
        root_b / "discord" / "channel.json",
    )

    assert source_a == "discord/channel.json"
    assert source_b == source_a


def test_message_failure_source_has_stable_suffix() -> None:
    root = Path("/srv/haxlab/raw")

    assert canonical_failure_source(
        root,
        root / "channel.json",
        message_id="1552774764110815262",
    ) == "channel.json#message:1552774764110815262"
    assert canonical_failure_source(
        root,
        root / "channel.json",
        message_id="unknown",
    ) == "channel.json#message:unknown"


def test_manifest_failure_evidence_is_byte_stable_after_root_relocation() -> None:
    def render(root: Path) -> bytes:
        source = canonical_failure_source(
            root,
            root / "nested" / "broken.json",
            message_id="unknown",
        )
        payload = {
            "failures": [
                {
                    "source": source,
                    "stage": "discord_message",
                    "error": "invalid fixture",
                }
            ],
            "schema_version": 1,
        }
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    first = render(Path("/srv/a/raw"))
    second = render(Path("/opt/b/raw"))

    assert first == second
    assert b"/srv/a" not in first
    assert b"/opt/b" not in second


def test_outside_root_path_fails_closed() -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError, match="contained by export_root"):
        canonical_failure_source(root, Path("/srv/haxlab/raw-other/channel.json"))


def test_export_root_itself_is_not_a_failure_file() -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError, match="file below export_root"):
        canonical_failure_source(root, root)


@pytest.mark.parametrize(
    "message_id",
    ["", " message", "message ", "message#other", "line\nbreak", "\x00bad", "\x7fbad"],
)
def test_ambiguous_message_tokens_fail_closed(message_id: str) -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError, match="canonical source token"):
        canonical_failure_source(
            root,
            root / "channel.json",
            message_id=message_id,
        )


def test_non_path_inputs_fail_closed() -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(TypeError, match="export_root"):
        canonical_failure_source(
            "/srv/haxlab/raw",  # type: ignore[arg-type]
            root / "channel.json",
        )
    with pytest.raises(TypeError, match="source_path"):
        canonical_failure_source(
            root,
            "/srv/haxlab/raw/channel.json",  # type: ignore[arg-type]
        )


def test_lexical_path_aliases_canonicalize_inside_root() -> None:
    root = Path("/srv/haxlab/raw")

    assert canonical_failure_source(
        root,
        root / "nested" / ".." / "channel.json",
    ) == "channel.json"
    assert canonical_failure_source(
        root / ".",
        root / "discord" / "." / "channel.json",
    ) == "discord/channel.json"


def test_lexical_parent_escape_fails_closed() -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError, match="contained by export_root"):
        canonical_failure_source(
            root,
            root / ".." / "outside.json",
        )
