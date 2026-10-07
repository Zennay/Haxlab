from __future__ import annotations

import os
from pathlib import Path


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_message_id(message_id: str) -> str:
    if (
        not isinstance(message_id, str)
        or not message_id
        or message_id != message_id.strip()
        or "#" in message_id
        or any(ord(character) < 32 for character in message_id)
    ):
        raise ValueError("message_id must be a non-empty canonical source token")
    return message_id


def canonical_failure_source(
    export_root: Path,
    source_path: Path,
    *,
    message_id: str | None = None,
) -> str:
    """Return deterministic root-relative provenance for one Discord import failure.

    The result never includes the absolute export root. When message_id is
    supplied, the stable suffix is #message:<id>.
    """

    if not isinstance(export_root, Path):
        raise TypeError("export_root must be a pathlib.Path")
    if not isinstance(source_path, Path):
        raise TypeError("source_path must be a pathlib.Path")

    root = _absolute_lexical(export_root)
    source = _absolute_lexical(source_path)

    try:
        relative = source.relative_to(root)
    except ValueError as exc:
        raise ValueError("source_path must be contained by export_root") from exc

    if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source_path must identify a file below export_root")

    provenance = relative.as_posix()
    if message_id is None:
        return provenance

    return f"{provenance}#message:{_require_message_id(message_id)}"
