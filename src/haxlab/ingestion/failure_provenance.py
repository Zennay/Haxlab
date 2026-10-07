from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

from haxlab.models import ImportFailure, ImportManifest


_MESSAGE_MARKER = "#message:"


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_message_id(message_id: str) -> str:
    if (
        not isinstance(message_id, str)
        or not message_id
        or message_id != message_id.strip()
        or "#" in message_id
        or any(ord(character) < 32 or ord(character) == 127 for character in message_id)
    ):
        raise ValueError("message_id must be a non-empty canonical source token")
    return message_id


def canonical_failure_source(
    export_root: Path,
    source_path: Path,
    *,
    message_id: str | None = None,
) -> str:
    """Return deterministic root-relative provenance for one import failure.

    Absolute source paths must be contained by ``export_root``. Relative source paths
    are interpreted relative to ``export_root``. Optional message provenance uses the
    stable suffix ``#message:<id>``.
    """

    if not isinstance(export_root, Path):
        raise TypeError("export_root must be a pathlib.Path")
    if not isinstance(source_path, Path):
        raise TypeError("source_path must be a pathlib.Path")

    root = _absolute_lexical(export_root)
    source = (
        _absolute_lexical(source_path)
        if source_path.is_absolute()
        else _absolute_lexical(root / source_path)
    )

    try:
        relative = source.relative_to(root)
    except ValueError as exc:
        raise ValueError("source_path must be contained by export_root") from exc

    if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source_path must identify a file below export_root")

    provenance = relative.as_posix()
    if message_id is None:
        return provenance

    return f"{provenance}{_MESSAGE_MARKER}{_require_message_id(message_id)}"


def canonical_failure_reference(export_root: Path, source: str) -> str:
    """Canonicalize an existing ImportFailure.source string without losing suffixes."""

    if not isinstance(source, str) or not source:
        raise ValueError("failure source must be a non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in source):
        raise ValueError("failure source must not contain control characters")

    marker_count = source.count(_MESSAGE_MARKER)
    if marker_count > 1:
        raise ValueError("failure source has ambiguous message provenance")
    if marker_count == 1:
        path_text, message_id = source.rsplit(_MESSAGE_MARKER, 1)
        if not path_text:
            raise ValueError("failure source path must be non-empty")
        return canonical_failure_source(
            export_root,
            Path(path_text),
            message_id=message_id,
        )

    return canonical_failure_source(export_root, Path(source))


def normalize_import_manifest_failures(
    export_root: Path,
    manifest: ImportManifest,
) -> ImportManifest:
    """Return a copy of an import manifest with canonicalized failure sources only."""

    if not isinstance(manifest, ImportManifest):
        raise TypeError("manifest must be an ImportManifest")

    failures = [
        ImportFailure(
            source=canonical_failure_reference(export_root, failure.source),
            stage=failure.stage,
            error=failure.error,
        )
        for failure in manifest.failures
    ]
    return replace(
        manifest,
        unmatched_replays=list(manifest.unmatched_replays),
        unmatched_reports=list(manifest.unmatched_reports),
        failures=failures,
    )


def write_normalized_manifest(
    export_root: Path,
    output_root: Path,
    manifest: ImportManifest,
) -> ImportManifest:
    """Canonicalize failure provenance and replace only the derived manifest file."""

    output_root = Path(output_root)
    normalized = normalize_import_manifest_failures(export_root, manifest)
    manifest_path = output_root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("derived manifest.json must already exist")

    payload = json.dumps(
        normalized.as_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    manifest_path.write_text(payload, encoding="utf-8")
    return normalized
