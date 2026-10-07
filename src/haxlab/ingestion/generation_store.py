from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat

from haxlab.ingestion.dataset_receipt import (
    M0_ARTIFACTS,
    DatasetReceiptError,
    build_dataset_receipt,
)
from haxlab.ingestion.generation_commit import (
    CURRENT_GENERATION_POINTER_FILE,
    GENERATION_COMMIT_FILE,
    GenerationCommitError,
    GENERATIONS_DIRECTORY,
    MAX_GENERATION_COMMIT_BYTES,
    MAX_GENERATION_POINTER_BYTES,
    build_generation_commit,
    generation_commit_bytes,
    generation_pointer_bytes,
    parse_generation_commit_bytes,
    parse_generation_pointer_bytes,
    validate_generation_pointer,
)


PUBLICATION_STAGES = (
    "after_stage_artifacts",
    "after_stage_commit",
    "after_generation_publish",
    "after_pointer_temp",
    "after_pointer_swap",
)


class GenerationStoreError(RuntimeError):
    """Raised when an M0 generation cannot be published or resolved safely."""


@dataclass(frozen=True)
class ResolvedGeneration:
    root: Path
    generation_id: str
    receipt_sha256: str
    commit_sha256: str


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if value is None:
        raise GenerationStoreError(f"{name} is required for generation storage")
    return int(value)


def _directory_flags() -> int:
    return os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW")


def _open_directory(path: Path) -> int:
    try:
        return os.open(path, _directory_flags())
    except OSError as exc:
        raise GenerationStoreError(f"unsafe or unreadable directory: {path}") from exc


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise GenerationStoreError(f"unsafe or unreadable directory entry: {name}") from exc


def _metadata_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular_at(
    parent_fd: int,
    name: str,
    *,
    max_bytes: int,
) -> bytes:
    flags = os.O_RDONLY | _required_flag("O_NOFOLLOW")
    flags |= _required_flag("O_NONBLOCK")
    fd = -1
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise GenerationStoreError(f"not a regular file: {name}")
        if before.st_size < 0 or before.st_size > max_bytes:
            raise GenerationStoreError(f"file exceeds generation evidence bound: {name}")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        if len(payload) > max_bytes:
            raise GenerationStoreError(f"file exceeds generation evidence bound: {name}")
        if _metadata_signature(before) != _metadata_signature(after):
            raise GenerationStoreError(f"generation evidence changed while reading: {name}")
        if len(payload) != after.st_size:
            raise GenerationStoreError(f"generation evidence size drift: {name}")
        return payload
    except GenerationStoreError:
        raise
    except OSError as exc:
        raise GenerationStoreError(f"unsafe or unreadable file: {name}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise GenerationStoreError("short write while publishing generation")
        view = view[written:]


def _write_new_regular_at(parent_fd: int, name: str, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | _required_flag("O_NOFOLLOW")
    )
    fd = -1
    created = False
    try:
        fd = os.open(name, flags, 0o644, dir_fd=parent_fd)
        created = True
        _write_all(fd, payload)
        os.fsync(fd)
    except (GenerationStoreError, OSError) as exc:
        if fd >= 0:
            os.close(fd)
            fd = -1
        if created:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        if isinstance(exc, GenerationStoreError):
            raise
        raise GenerationStoreError(f"failed to publish file: {name}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _copy_regular_at(source_fd: int, destination_fd: int, name: str) -> None:
    read_flags = (
        os.O_RDONLY
        | _required_flag("O_NOFOLLOW")
        | _required_flag("O_NONBLOCK")
    )
    source = -1
    destination = -1
    try:
        source = os.open(name, read_flags, dir_fd=source_fd)
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode):
            raise GenerationStoreError(f"source artifact is not regular: {name}")

        destination = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _required_flag("O_NOFOLLOW"),
            0o644,
            dir_fd=destination_fd,
        )
        copied = 0
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            _write_all(destination, chunk)
            copied += len(chunk)
        os.fsync(destination)

        after = os.fstat(source)
        if _metadata_signature(before) != _metadata_signature(after):
            raise GenerationStoreError(f"source artifact changed while copying: {name}")
        if copied != after.st_size:
            raise GenerationStoreError(f"source artifact size drift: {name}")
    except GenerationStoreError:
        raise
    except OSError as exc:
        raise GenerationStoreError(f"failed to copy source artifact: {name}") from exc
    finally:
        if destination >= 0:
            os.close(destination)
        if source >= 0:
            os.close(source)


def _ensure_generations_directory(store_fd: int) -> int:
    try:
        os.mkdir(GENERATIONS_DIRECTORY, 0o755, dir_fd=store_fd)
        os.fsync(store_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise GenerationStoreError("failed to create generations directory") from exc
    return _open_directory_at(store_fd, GENERATIONS_DIRECTORY)


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise GenerationStoreError(f"failed to inspect store entry: {name}") from exc
    return True


def _preflight_dataset_root(root: Path) -> None:
    root_fd = _open_directory(root)
    try:
        for name in M0_ARTIFACTS:
            fd = -1
            try:
                fd = os.open(
                    name,
                    os.O_RDONLY
                    | _required_flag("O_NOFOLLOW")
                    | _required_flag("O_NONBLOCK"),
                    dir_fd=root_fd,
                )
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise GenerationStoreError(
                        f"dataset artifact is not a regular file: {name}"
                    )
            except GenerationStoreError:
                raise
            except OSError as exc:
                raise GenerationStoreError(
                    f"unsafe or unreadable dataset artifact: {name}"
                ) from exc
            finally:
                if fd >= 0:
                    os.close(fd)
    finally:
        os.close(root_fd)


def _cleanup_stage(generations_fd: int, stage_name: str) -> None:
    stage_fd = -1
    try:
        stage_fd = _open_directory_at(generations_fd, stage_name)
        for name in (*M0_ARTIFACTS, GENERATION_COMMIT_FILE):
            try:
                os.unlink(name, dir_fd=stage_fd)
            except FileNotFoundError:
                pass
        os.fsync(stage_fd)
    except (GenerationStoreError, OSError):
        return
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
    try:
        os.rmdir(stage_name, dir_fd=generations_fd)
        os.fsync(generations_fd)
    except OSError:
        pass


def _emit_fault(_fault: Callable[[str], None] | None, stage: str) -> None:
    if _fault is not None:
        _fault(stage)


def _validate_existing_generation(
    generation_root: Path,
    expected_commit: dict[str, object],
) -> None:
    generation_fd = _open_directory(generation_root)
    try:
        commit_payload = _read_regular_at(
            generation_fd,
            GENERATION_COMMIT_FILE,
            max_bytes=MAX_GENERATION_COMMIT_BYTES,
        )
    finally:
        os.close(generation_fd)
    try:
        commit = parse_generation_commit_bytes(commit_payload)
        if commit != expected_commit:
            raise GenerationStoreError("existing generation commit does not match receipt")
        _preflight_dataset_root(generation_root)
        receipt = build_dataset_receipt(generation_root)
        if build_generation_commit(receipt) != expected_commit:
            raise GenerationStoreError("existing generation artifacts do not match commit")
    except GenerationStoreError:
        raise
    except (DatasetReceiptError, GenerationCommitError) as exc:
        raise GenerationStoreError("existing generation evidence is invalid") from exc


def publish_generation(
    source_root: Path,
    store_root: Path,
    *,
    _fault: Callable[[str], None] | None = None,
) -> ResolvedGeneration:
    """Publish one immutable M0 generation and atomically select it for readers."""

    source_root = Path(source_root)
    store_root = Path(store_root)
    try:
        _preflight_dataset_root(source_root)
        source_receipt = build_dataset_receipt(source_root)
        commit = build_generation_commit(source_receipt)
    except (DatasetReceiptError, GenerationCommitError) as exc:
        raise GenerationStoreError("source dataset evidence is invalid") from exc
    generation_id = str(commit["generation_id"])

    try:
        resolved_source = source_root.resolve(strict=True)
        prospective_store = store_root.resolve(strict=False)
    except OSError as exc:
        raise GenerationStoreError("source/store paths cannot be resolved safely") from exc
    if prospective_store == resolved_source or prospective_store.is_relative_to(
        resolved_source
    ):
        raise GenerationStoreError("store_root must be outside source_root")

    try:
        store_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GenerationStoreError(f"failed to create store root: {store_root}") from exc

    try:
        resolved_store = store_root.resolve(strict=True)
    except OSError as exc:
        raise GenerationStoreError("store_root cannot be resolved safely") from exc
    if resolved_store == resolved_source or resolved_store.is_relative_to(resolved_source):
        raise GenerationStoreError("store_root must be outside source_root")

    source_fd = -1
    store_fd = -1
    generations_fd = -1
    stage_name: str | None = None
    pointer_temp_name: str | None = None
    try:
        source_fd = _open_directory(source_root)
        store_fd = _open_directory(store_root)
        generations_fd = _ensure_generations_directory(store_fd)
        generation_root = store_root / GENERATIONS_DIRECTORY / generation_id

        if _entry_exists(generations_fd, generation_id):
            _validate_existing_generation(generation_root, commit)
        else:
            for _ in range(32):
                candidate = f".staging-{generation_id}-{secrets.token_hex(8)}"
                try:
                    os.mkdir(candidate, 0o755, dir_fd=generations_fd)
                    stage_name = candidate
                    break
                except FileExistsError:
                    continue
            if stage_name is None:
                raise GenerationStoreError("could not allocate generation staging directory")

            stage_fd = _open_directory_at(generations_fd, stage_name)
            try:
                for name in M0_ARTIFACTS:
                    _copy_regular_at(source_fd, stage_fd, name)
                os.fsync(stage_fd)
                _emit_fault(_fault, "after_stage_artifacts")

                staged_root = store_root / GENERATIONS_DIRECTORY / stage_name
                staged_receipt = build_dataset_receipt(staged_root)
                if staged_receipt != source_receipt:
                    raise GenerationStoreError("staged generation receipt does not match source")

                _write_new_regular_at(
                    stage_fd,
                    GENERATION_COMMIT_FILE,
                    generation_commit_bytes(source_receipt),
                )
                os.fsync(stage_fd)
                _emit_fault(_fault, "after_stage_commit")
            finally:
                os.close(stage_fd)

            try:
                os.rename(
                    stage_name,
                    generation_id,
                    src_dir_fd=generations_fd,
                    dst_dir_fd=generations_fd,
                )
                stage_name = None
            except OSError as exc:
                if not _entry_exists(generations_fd, generation_id):
                    raise GenerationStoreError(
                        "failed to publish immutable generation"
                    ) from exc
                _validate_existing_generation(generation_root, commit)
                _cleanup_stage(generations_fd, stage_name)
                stage_name = None
            os.fsync(generations_fd)
            _emit_fault(_fault, "after_generation_publish")
            _validate_existing_generation(generation_root, commit)

        pointer_payload = generation_pointer_bytes(commit)
        for _ in range(32):
            candidate = f".{CURRENT_GENERATION_POINTER_FILE}.{secrets.token_hex(8)}.tmp"
            try:
                _write_new_regular_at(store_fd, candidate, pointer_payload)
                pointer_temp_name = candidate
                break
            except GenerationStoreError as exc:
                if _entry_exists(store_fd, candidate):
                    continue
                raise exc
        if pointer_temp_name is None:
            raise GenerationStoreError("could not allocate generation pointer temp file")

        _emit_fault(_fault, "after_pointer_temp")
        try:
            os.replace(
                pointer_temp_name,
                CURRENT_GENERATION_POINTER_FILE,
                src_dir_fd=store_fd,
                dst_dir_fd=store_fd,
            )
        except OSError as exc:
            raise GenerationStoreError("failed to atomically replace generation pointer") from exc
        pointer_temp_name = None
        os.fsync(store_fd)
        _emit_fault(_fault, "after_pointer_swap")
    finally:
        if pointer_temp_name is not None and store_fd >= 0:
            try:
                os.unlink(pointer_temp_name, dir_fd=store_fd)
            except OSError:
                pass
        if stage_name is not None and generations_fd >= 0:
            _cleanup_stage(generations_fd, stage_name)
        if generations_fd >= 0:
            os.close(generations_fd)
        if store_fd >= 0:
            os.close(store_fd)
        if source_fd >= 0:
            os.close(source_fd)

    return resolve_current_generation(store_root)


def resolve_current_generation(store_root: Path) -> ResolvedGeneration:
    """Resolve one reader-visible M0 generation through pointer, commit and receipt."""

    store_root = Path(store_root)
    store_fd = _open_directory(store_root)
    generations_fd = -1
    generation_fd = -1
    try:
        pointer_payload = _read_regular_at(
            store_fd,
            CURRENT_GENERATION_POINTER_FILE,
            max_bytes=MAX_GENERATION_POINTER_BYTES,
        )
        try:
            pointer = parse_generation_pointer_bytes(pointer_payload)
        except GenerationCommitError as exc:
            raise GenerationStoreError("current generation pointer is invalid") from exc

        generations_fd = _open_directory_at(store_fd, GENERATIONS_DIRECTORY)
        generation_id = str(pointer["generation_id"])
        generation_fd = _open_directory_at(generations_fd, generation_id)
        commit_payload = _read_regular_at(
            generation_fd,
            GENERATION_COMMIT_FILE,
            max_bytes=MAX_GENERATION_COMMIT_BYTES,
        )
        try:
            commit = parse_generation_commit_bytes(commit_payload)
            validate_generation_pointer(pointer, commit)
        except GenerationCommitError as exc:
            raise GenerationStoreError("current generation commit is invalid") from exc
    finally:
        if generation_fd >= 0:
            os.close(generation_fd)
        if generations_fd >= 0:
            os.close(generations_fd)
        os.close(store_fd)

    generation_root = store_root / GENERATIONS_DIRECTORY / str(pointer["generation_id"])
    try:
        _preflight_dataset_root(generation_root)
        receipt = build_dataset_receipt(generation_root)
        if build_generation_commit(receipt) != commit:
            raise GenerationStoreError("resolved generation artifacts do not match commit")
    except GenerationStoreError:
        raise
    except (DatasetReceiptError, GenerationCommitError) as exc:
        raise GenerationStoreError("resolved generation evidence is invalid") from exc

    return ResolvedGeneration(
        root=generation_root,
        generation_id=str(pointer["generation_id"]),
        receipt_sha256=str(pointer["receipt_sha256"]),
        commit_sha256=str(pointer["commit_sha256"]),
    )
