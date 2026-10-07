from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.ingestion.failure_provenance import (
    canonical_failure_reference,
    canonical_failure_source,
    normalize_import_manifest_failures,
    write_normalized_manifest,
)
from haxlab.models import ImportFailure, ImportManifest


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


def test_relative_failure_source_is_interpreted_from_export_root() -> None:
    root = Path("/srv/haxlab/raw")

    assert canonical_failure_source(
        root,
        Path("discord/channel.json"),
    ) == "discord/channel.json"
    assert canonical_failure_source(
        root,
        Path("nested/../channel.json"),
    ) == "channel.json"


def test_relative_failure_parent_escape_fails_closed() -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError, match="contained by export_root"):
        canonical_failure_source(root, Path("../outside.json"))


def test_existing_absolute_message_reference_canonicalizes() -> None:
    root = Path("/srv/haxlab/raw")

    assert canonical_failure_reference(
        root,
        "/srv/haxlab/raw/discord/channel.json#message:1552774764110815262",
    ) == "discord/channel.json#message:1552774764110815262"


def test_existing_relative_failure_reference_is_idempotent() -> None:
    root = Path("/srv/haxlab/raw")
    source = "discord/channel.json#message:unknown"

    assert canonical_failure_reference(root, source) == source
    assert canonical_failure_reference(
        root,
        canonical_failure_reference(root, source),
    ) == source


@pytest.mark.parametrize(
    "source",
    [
        "",
        "#message:123",
        "channel.json#message:",
        "channel.json#message:123#message:456",
        "channel.json#message:bad#token",
        "channel.json\n",
        "channel.json\x7f",
    ],
)
def test_malformed_existing_failure_references_fail_closed(source: str) -> None:
    root = Path("/srv/haxlab/raw")

    with pytest.raises(ValueError):
        canonical_failure_reference(root, source)


def test_manifest_normalizer_changes_only_failure_sources() -> None:
    root = Path("/srv/haxlab/raw")
    original = ImportManifest(
        replay_count=3,
        unique_replay_count=2,
        duplicate_replay_count=1,
        report_count=1,
        match_count=1,
        unmatched_replays=["unmatched.hbr2"],
        unmatched_reports=["123"],
        failures=[
            ImportFailure(
                source="/srv/haxlab/raw/channel.json#message:123",
                stage="discord_message",
                error="broken",
            ),
            ImportFailure(
                source="bad.hbr2",
                stage="hbr2_validation",
                error="invalid",
            ),
        ],
    )

    normalized = normalize_import_manifest_failures(root, original)

    assert normalized is not original
    assert normalized.as_dict() == {
        **original.as_dict(),
        "failures": [
            {
                "source": "channel.json#message:123",
                "stage": "discord_message",
                "error": "broken",
            },
            {
                "source": "bad.hbr2",
                "stage": "hbr2_validation",
                "error": "invalid",
            },
        ],
    }
    assert original.failures[0].source == "/srv/haxlab/raw/channel.json#message:123"
    assert normalized.unmatched_replays is not original.unmatched_replays
    assert normalized.unmatched_reports is not original.unmatched_reports


def test_manifest_normalization_is_byte_stable_across_roots(tmp_path: Path) -> None:
    outputs: list[bytes] = []
    for label in ("host-a", "host-b"):
        root = tmp_path / label / "raw"
        out = tmp_path / label / "derived"
        root.mkdir(parents=True)
        out.mkdir(parents=True)
        manifest = ImportManifest(
            failures=[
                ImportFailure(
                    source=f"{root}/nested/broken.json#message:unknown",
                    stage="discord_message",
                    error="invalid fixture",
                )
            ]
        )
        (out / "manifest.json").write_text("{}\n", encoding="utf-8")

        normalized = write_normalized_manifest(root, out, manifest)
        outputs.append((out / "manifest.json").read_bytes())

        assert normalized.failures[0].source == (
            "nested/broken.json#message:unknown"
        )
        assert str(root).encode() not in outputs[-1]

    assert outputs[0] == outputs[1]


def test_manifest_writer_requires_existing_derived_manifest(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    out = tmp_path / "derived"
    root.mkdir()
    out.mkdir()

    with pytest.raises(ValueError, match="manifest.json must already exist"):
        write_normalized_manifest(root, out, ImportManifest())
