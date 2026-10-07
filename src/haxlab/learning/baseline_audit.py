from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import stat
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


AUDIT_SCHEMA = "haxlab-bc-baseline-audit-v1"
MODEL_SCHEMA = "haxlab-bc-baseline-v1"
MODEL_ARRAYS = ("mean", "std", "w1", "b1", "wd", "bd", "wk", "bk")
EXPECTED_EXCLUDED_COLUMNS = (
    "frame",
    "player_index",
    "team_id",
    "dir_x",
    "dir_y",
    "kick",
)
ACTION_DIRS = tuple(
    (dx, dy)
    for dy in (-1, 0, 1)
    for dx in (-1, 0, 1)
)
MAX_METRICS_BYTES = 8 * 1024 * 1024
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_MODEL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024


class BaselineAuditError(ValueError):
    """Raised when a published baseline bundle violates its contract."""


def _fail(message: str) -> None:
    raise BaselineAuditError(message)


def _native_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{field} must be a native integer >= {minimum}")
    return value


def _finite(
    value: Any,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) not in (int, float):
        _fail(f"{field} must be a native finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{field} must be a native finite number")
    if minimum is not None and number < minimum:
        _fail(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        _fail(f"{field} must be <= {maximum}")
    return number


def _canonical_string(value: Any, *, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        _fail(f"{field} must be a non-empty canonical string")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    _fail(f"invalid JSON numeric constant: {value}")


def _open_bundle_root(path: Path) -> tuple[int, tuple[int, int]]:
    if not isinstance(path, Path):
        _fail("bundle directory must be pathlib.Path")
    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"bundle directory is not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISDIR(initial.st_mode):
        _fail("bundle directory must be a regular non-symlink directory")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        _fail("platform lacks required directory no-follow support")

    flags = os.O_RDONLY | nofollow | directory
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        _fail(f"bundle directory secure open failed: {exc}")

    try:
        opened = os.fstat(fd)
        if not stat.S_ISDIR(opened.st_mode):
            _fail("bundle directory descriptor is not a directory")
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            _fail("bundle directory identity changed during secure open")
        return fd, (opened.st_dev, opened.st_ino)
    except Exception:
        os.close(fd)
        raise


def _reconfirm_bundle_root(
    path: Path,
    *,
    root_fd: int,
    identity: tuple[int, int],
) -> None:
    try:
        opened = os.fstat(root_fd)
    except OSError as exc:
        _fail(f"bundle directory descriptor became unreadable: {exc}")
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != identity
    ):
        _fail("bundle directory descriptor identity changed during audit")

    try:
        current = path.lstat()
    except OSError as exc:
        _fail(f"bundle directory path changed during audit: {exc}")
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        _fail("bundle directory identity changed during audit")


def _secure_bytes(
    root_fd: int,
    *,
    name: str,
    max_bytes: int,
) -> tuple[bytes, tuple[int, int]]:
    if (
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        _fail("artifact name must be one canonical path component")

    try:
        initial = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as exc:
        _fail(f"{name}: not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail(f"{name}: must be a regular non-symlink file")
    if initial.st_size > max_bytes:
        _fail(f"{name}: exceeds {max_bytes} byte limit")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        _fail("platform lacks required O_NOFOLLOW")
    flags = os.O_RDONLY | nofollow
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError as exc:
        _fail(f"{name}: secure open failed: {exc}")

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"{name}: descriptor is not a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            _fail(f"{name}: identity changed during secure open")
        if before.st_size > max_bytes:
            _fail(f"{name}: exceeds {max_bytes} byte limit")

        def read_once() -> bytes:
            os.lseek(fd, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    _fail(f"{name}: exceeds {max_bytes} byte limit")
            return b"".join(chunks)

        first = read_once()
        second = read_once()
        after = os.fstat(fd)
        if first != second:
            _fail(f"{name}: bytes changed during audit")
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            _fail(f"{name}: metadata changed during audit")
        if len(first) != after.st_size:
            _fail(f"{name}: byte count does not match file size")

        try:
            current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except OSError as exc:
            _fail(f"{name}: path changed during audit: {exc}")
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino, current.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            _fail(f"{name}: logical path identity changed during audit")
        return first, (after.st_dev, after.st_ino)
    except OSError as exc:
        _fail(f"{name}: read failed: {exc}")
    finally:
        os.close(fd)

def _load_metrics(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"metrics.json: invalid UTF-8: {exc}")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"metrics.json: invalid JSON: {exc}")
    if type(value) is not dict:
        _fail("metrics.json: root must be a JSON object")
    return value


def _load_model(payload: bytes) -> dict[str, np.ndarray]:
    expected_members = {f"{name}.npy" for name in MODEL_ARRAYS}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                _fail("model.npz: duplicate ZIP member")
            if set(names) != expected_members:
                missing = sorted(expected_members - set(names))
                extra = sorted(set(names) - expected_members)
                _fail(
                    f"model.npz: array inventory mismatch; "
                    f"missing={missing} extra={extra}"
                )
            total_uncompressed = 0
            for info in infos:
                if info.is_dir() or info.flag_bits & 0x1:
                    _fail("model.npz: unsafe ZIP member")
                if info.compress_type not in (
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                ):
                    _fail("model.npz: unsupported ZIP compression")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_MODEL_UNCOMPRESSED_BYTES:
                    _fail("model.npz: uncompressed size limit exceeded")
    except zipfile.BadZipFile as exc:
        _fail(f"model.npz: invalid ZIP container: {exc}")

    try:
        with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
            if set(archive.files) != set(MODEL_ARRAYS):
                _fail("model.npz: NumPy array inventory mismatch")
            arrays = {
                name: np.array(archive[name], copy=True)
                for name in MODEL_ARRAYS
            }
    except Exception as exc:
        _fail(f"model.npz: cannot load arrays: {exc}")
    return arrays


def _validate_direction_classes(value: Any) -> None:
    expected = [
        {"class_id": index, "dir_x": dx, "dir_y": dy}
        for index, (dx, dy) in enumerate(ACTION_DIRS)
    ]
    if value != expected:
        _fail("direction_classes must match canonical 9-way mapping")


def _validate_metric_block(value: Any, *, field: str) -> dict[str, Any]:
    required = {
        "samples",
        "direction_accuracy",
        "joint_accuracy",
        "kick_precision",
        "kick_recall",
        "kick_f1",
        "kick_true_rate",
        "kick_predicted_rate",
        "kick_threshold",
        "kick_confusion",
        "baselines",
    }
    if type(value) is not dict or set(value) != required:
        _fail(f"{field} has unsupported fields")
    samples = _native_int(value["samples"], field=f"{field}.samples", minimum=1)
    for key in (
        "direction_accuracy",
        "joint_accuracy",
        "kick_precision",
        "kick_recall",
        "kick_f1",
        "kick_true_rate",
        "kick_predicted_rate",
        "kick_threshold",
    ):
        _finite(value[key], field=f"{field}.{key}", minimum=0.0, maximum=1.0)

    confusion = value["kick_confusion"]
    if type(confusion) is not dict or set(confusion) != {"tp", "fp", "fn", "tn"}:
        _fail(f"{field}.kick_confusion has unsupported fields")
    counts = {
        key: _native_int(confusion[key], field=f"{field}.kick_confusion.{key}")
        for key in ("tp", "fp", "fn", "tn")
    }
    if sum(counts.values()) != samples:
        _fail(f"{field}.kick_confusion does not sum to samples")

    tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
    expected = {
        "kick_precision": precision,
        "kick_recall": recall,
        "kick_f1": f1,
        "kick_true_rate": (tp + fn) / samples,
        "kick_predicted_rate": (tp + fp) / samples,
    }
    for key, expected_value in expected.items():
        if not math.isclose(
            float(value[key]),
            expected_value,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail(f"{field}.{key} is inconsistent with confusion counts")

    baselines = value["baselines"]
    if type(baselines) is not dict or set(baselines) != {
        "majority_direction_accuracy",
        "always_no_kick_accuracy",
    }:
        _fail(f"{field}.baselines has unsupported fields")
    _finite(
        baselines["majority_direction_accuracy"],
        field=f"{field}.baselines.majority_direction_accuracy",
        minimum=0.0,
        maximum=1.0,
    )
    no_kick = _finite(
        baselines["always_no_kick_accuracy"],
        field=f"{field}.baselines.always_no_kick_accuracy",
        minimum=0.0,
        maximum=1.0,
    )
    if not math.isclose(
        no_kick,
        (tn + fp) / samples,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        _fail(f"{field}.baselines.always_no_kick_accuracy is inconsistent")
    return value


def _validate_training(value: Any) -> dict[str, Any]:
    required = {
        "seed",
        "calibrated_kick_threshold",
        "epochs",
        "batch_size",
        "learning_rate",
        "l2",
        "train_replays",
        "holdout_replays",
        "train_stats",
    }
    if type(value) is not dict or set(value) != required:
        _fail("training has unsupported fields")
    _native_int(value["seed"], field="training.seed")
    _finite(
        value["calibrated_kick_threshold"],
        field="training.calibrated_kick_threshold",
        minimum=0.0,
        maximum=1.0,
    )
    _native_int(value["epochs"], field="training.epochs", minimum=1)
    _native_int(value["batch_size"], field="training.batch_size", minimum=1)
    _finite(value["learning_rate"], field="training.learning_rate", minimum=0.0)
    _finite(value["l2"], field="training.l2", minimum=0.0)
    _native_int(value["train_replays"], field="training.train_replays", minimum=1)
    _native_int(value["holdout_replays"], field="training.holdout_replays", minimum=1)

    stats = value["train_stats"]
    required_stats = {
        "samples",
        "direction_counts",
        "kick_positive",
        "kick_negative",
        "kick_positive_rate",
        "kick_positive_weight",
    }
    if type(stats) is not dict or set(stats) != required_stats:
        _fail("training.train_stats has unsupported fields")
    samples = _native_int(
        stats["samples"],
        field="training.train_stats.samples",
        minimum=1,
    )
    direction_counts = stats["direction_counts"]
    if type(direction_counts) is not list or len(direction_counts) != 9:
        _fail("training.train_stats.direction_counts must contain 9 integers")
    dirs = [
        _native_int(
            count,
            field=f"training.train_stats.direction_counts[{index}]",
        )
        for index, count in enumerate(direction_counts)
    ]
    if sum(dirs) != samples:
        _fail("training.train_stats.direction_counts do not sum to samples")
    positive = _native_int(
        stats["kick_positive"],
        field="training.train_stats.kick_positive",
    )
    negative = _native_int(
        stats["kick_negative"],
        field="training.train_stats.kick_negative",
    )
    if positive + negative != samples:
        _fail("training.train_stats kick counts do not sum to samples")
    rate = _finite(
        stats["kick_positive_rate"],
        field="training.train_stats.kick_positive_rate",
        minimum=0.0,
        maximum=1.0,
    )
    if not math.isclose(
        rate,
        positive / samples,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        _fail("training.train_stats.kick_positive_rate is inconsistent")
    _finite(
        stats["kick_positive_weight"],
        field="training.train_stats.kick_positive_weight",
        minimum=1.0,
        maximum=25.0,
    )
    return value


def _array_sha256(array: np.ndarray) -> str:
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(
        header + b"\0" + array.tobytes(order="C")
    ).hexdigest()


def _validate_bundle(
    metrics: dict[str, Any],
    arrays: dict[str, np.ndarray],
) -> dict[str, Any]:
    required_top = {
        "schema",
        "created_at",
        "model_path",
        "train_index",
        "holdout_index",
        "input_columns",
        "excluded_input_columns",
        "direction_classes",
        "architecture",
        "training",
        "history",
        "final_holdout",
    }
    if set(metrics) != required_top:
        _fail("metrics.json has unsupported top-level fields")
    if metrics["schema"] != MODEL_SCHEMA:
        _fail(f"unsupported baseline schema: {metrics['schema']!r}")

    created_at = _canonical_string(metrics["created_at"], field="created_at")
    try:
        timestamp = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
    except ValueError:
        _fail("created_at must be an ISO-8601 timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _fail("created_at must include timezone")

    model_path = _canonical_string(metrics["model_path"], field="model_path")
    if Path(model_path).name != "model.npz":
        _fail("model_path must end in model.npz")
    train_index = _canonical_string(metrics["train_index"], field="train_index")
    holdout_index = _canonical_string(
        metrics["holdout_index"],
        field="holdout_index",
    )
    if train_index == holdout_index:
        _fail("train_index and holdout_index must differ")

    input_columns = metrics["input_columns"]
    if (
        type(input_columns) is not list
        or not input_columns
        or any(
            type(item) is not str
            or not item
            or item != item.strip()
            for item in input_columns
        )
        or len(input_columns) != len(set(input_columns))
    ):
        _fail("input_columns must be unique non-empty canonical strings")
    if metrics["excluded_input_columns"] != list(EXPECTED_EXCLUDED_COLUMNS):
        _fail("excluded_input_columns does not match baseline contract")
    _validate_direction_classes(metrics["direction_classes"])

    architecture = metrics["architecture"]
    required_arch = {
        "type",
        "input_dim",
        "hidden_dim",
        "direction_classes",
        "kick_head",
    }
    if type(architecture) is not dict or set(architecture) != required_arch:
        _fail("architecture has unsupported fields")
    if architecture["type"] != "numpy_mlp_multitask":
        _fail("architecture.type is not canonical")
    if architecture["kick_head"] != "binary_sigmoid":
        _fail("architecture.kick_head is not canonical")
    input_dim = _native_int(
        architecture["input_dim"],
        field="architecture.input_dim",
        minimum=1,
    )
    hidden_dim = _native_int(
        architecture["hidden_dim"],
        field="architecture.hidden_dim",
        minimum=1,
    )
    if architecture["direction_classes"] != 9:
        _fail("architecture.direction_classes must be 9")
    if input_dim != len(input_columns):
        _fail("architecture.input_dim does not match input_columns")

    expected_shapes = {
        "mean": (input_dim,),
        "std": (input_dim,),
        "w1": (input_dim, hidden_dim),
        "b1": (hidden_dim,),
        "wd": (hidden_dim, 9),
        "bd": (9,),
        "wk": (hidden_dim, 1),
        "bk": (1,),
    }
    array_inventory: dict[str, Any] = {}
    for name in MODEL_ARRAYS:
        array = arrays[name]
        if array.dtype != np.dtype("float32"):
            _fail(f"model.{name} must be float32")
        if tuple(array.shape) != expected_shapes[name]:
            _fail(
                f"model.{name} shape mismatch: "
                f"{tuple(array.shape)} != {expected_shapes[name]}"
            )
        if not np.isfinite(array).all():
            _fail(f"model.{name} contains non-finite values")
        array_inventory[name] = {
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "sha256": _array_sha256(array),
        }
    if not np.all(arrays["std"] > 0.0):
        _fail("model.std must be strictly positive")

    training = _validate_training(metrics["training"])
    history = metrics["history"]
    epochs = training["epochs"]
    if type(history) is not list or len(history) != epochs:
        _fail("history length must match training.epochs")
    for index, row in enumerate(history, start=1):
        if type(row) is not dict or set(row) != {
            "epoch",
            "train_loss_mean",
            "direction_loss_mean",
            "kick_loss_mean",
            "holdout",
        }:
            _fail(f"history[{index - 1}] has unsupported fields")
        if row["epoch"] != index:
            _fail("history epochs must be canonical 1..N")
        for key in (
            "train_loss_mean",
            "direction_loss_mean",
            "kick_loss_mean",
        ):
            _finite(
                row[key],
                field=f"history[{index - 1}].{key}",
                minimum=0.0,
            )
        _validate_metric_block(
            row["holdout"],
            field=f"history[{index - 1}].holdout",
        )

    final_holdout = _validate_metric_block(
        metrics["final_holdout"],
        field="final_holdout",
    )
    if not math.isclose(
        float(final_holdout["kick_threshold"]),
        float(training["calibrated_kick_threshold"]),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        _fail(
            "final_holdout.kick_threshold does not match calibrated threshold"
        )

    semantic = {
        "schema": MODEL_SCHEMA,
        "input_columns": input_columns,
        "excluded_input_columns": metrics["excluded_input_columns"],
        "direction_classes": metrics["direction_classes"],
        "architecture": architecture,
        "training": training,
        "history": history,
        "final_holdout": final_holdout,
        "arrays": array_inventory,
    }
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "input_dim": input_dim,
        "hidden_dim": hidden_dim,
        "train_replays": training["train_replays"],
        "holdout_replays": training["holdout_replays"],
        "final_holdout_samples": final_holdout["samples"],
        "arrays": array_inventory,
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def audit_baseline_bundle(bundle_dir: Path) -> dict[str, Any]:
    root_fd, root_identity = _open_bundle_root(bundle_dir)
    try:
        metrics_bytes, metrics_identity = _secure_bytes(
            root_fd,
            name="metrics.json",
            max_bytes=MAX_METRICS_BYTES,
        )
        model_bytes, model_identity = _secure_bytes(
            root_fd,
            name="model.npz",
            max_bytes=MAX_MODEL_BYTES,
        )
        if metrics_identity == model_identity:
            _fail("metrics.json and model.npz must not alias the same file")

        _reconfirm_bundle_root(
            bundle_dir,
            root_fd=root_fd,
            identity=root_identity,
        )

        metrics = _load_metrics(metrics_bytes)
        arrays = _load_model(model_bytes)
        validated = _validate_bundle(metrics, arrays)

        _reconfirm_bundle_root(
            bundle_dir,
            root_fd=root_fd,
            identity=root_identity,
        )
        return {
            "schema": AUDIT_SCHEMA,
            "ok": True,
            "metrics_size_bytes": len(metrics_bytes),
            "metrics_sha256": hashlib.sha256(metrics_bytes).hexdigest(),
            "model_size_bytes": len(model_bytes),
            "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
            **validated,
        }
    finally:
        os.close(root_fd)

def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-baseline-audit")
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args()
    try:
        receipt = audit_baseline_bundle(args.bundle_dir)
    except BaselineAuditError as exc:
        print(
            json.dumps(
                {
                    "schema": AUDIT_SCHEMA,
                    "ok": False,
                    "error": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
