from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable, Sequence


PROTECTED_PREFIXES = (
    "src/haxlab/evaluation/",
    "configs/evaluation/",
    "docs/evaluation",
)

PROTECTED_EXACT_PATHS = frozenset(
    {
        ".github/workflows/ci.yml",
        ".github/workflows/closed-loop-arena-v2-calibration.yml",
        ".github/workflows/multisource-suite-v2.yml",
        "docs/closed-loop-arena-v2.md",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "src/haxlab/analysis/roles.py",
        "src/haxlab/hashing.py",
        "tests/test_closed_loop_arena_v2.js",
        "tools/elite_closed_loop_arena_v2.js",
        "tools/elite_features.js",
        "tools/elite_policy_runtime.js",
        "tools/elite_tactics.js",
        "tools/sandbox_replay_start.js",
    }
)

PROTECTED_TEST_TOKENS = (
    "arena_v2",
    "calibration",
    "closed_loop",
    "duel",
    "elite",
    "evaluation",
    "multisource",
    "promotion",
    "scenario_source",
)

_ALLOWED_DIFF_STATUSES = frozenset("ACDMRTUXB")
_EXACT_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ResyncGuardReport:
    base: str
    head: str
    changed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]

    @property
    def safe(self) -> bool:
        return not self.protected_paths


def _normalize_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    parsed = PurePosixPath(normalized)
    if normalized == "." or normalized.startswith("../") or parsed.is_absolute():
        raise ValueError(f"invalid repository path: {path!r}")
    return normalized


def is_evaluation_owned_path(path: str) -> bool:
    normalized = _normalize_path(path)

    if normalized in PROTECTED_EXACT_PATHS:
        return True
    if any(normalized.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return True

    if normalized.startswith("tests/test_") and normalized.endswith(".py"):
        filename = PurePosixPath(normalized).name.casefold()
        return any(token in filename for token in PROTECTED_TEST_TOKENS)

    return False


def evaluate_changed_paths(
    changed_paths: Iterable[str],
    *,
    base: str,
    head: str,
) -> ResyncGuardReport:
    normalized = tuple(
        sorted({_normalize_path(path) for path in changed_paths if path.strip()})
    )
    protected = tuple(path for path in normalized if is_evaluation_owned_path(path))
    return ResyncGuardReport(
        base=base,
        head=head,
        changed_paths=normalized,
        protected_paths=protected,
    )


def _paths_from_name_status(output: str) -> tuple[str, ...]:
    tokens = output.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()

    paths: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status or status[0] not in _ALLOWED_DIFF_STATUSES:
            raise ValueError(f"invalid git diff status: {status!r}")

        kind = status[0]
        if kind in {"R", "C"}:
            score = status[1:]
            if not score.isdigit() or not 0 <= int(score) <= 100:
                raise ValueError(f"invalid git diff similarity status: {status!r}")
            path_count = 2
        else:
            if len(status) != 1:
                raise ValueError(f"invalid git diff status: {status!r}")
            path_count = 1
        if index + path_count > len(tokens):
            raise ValueError(f"truncated git diff record for status {status!r}")

        for _ in range(path_count):
            path = tokens[index]
            index += 1
            if not path:
                raise ValueError(f"empty git diff path for status {status!r}")
            paths.append(path)

    return tuple(paths)


def _require_exact_commit_sha(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _EXACT_COMMIT_SHA_RE.fullmatch(value) is None:
        raise ValueError(
            f"{label} must be an exact lowercase 40-character commit SHA"
        )
    return value


def changed_paths_from_git(base: str, head: str) -> tuple[str, ...]:
    base_sha = _require_exact_commit_sha(base, label="base")
    head_sha = _require_exact_commit_sha(head, label="head")
    command = [
        "git",
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--find-copies",
        "--diff-filter=ACDMRTUXB",
        f"{base_sha}...{head_sha}",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"git diff failed for {base_sha}...{head_sha}: "
            f"{message or f'exit {completed.returncode}'}"
        )
    return _paths_from_name_status(completed.stdout)


def build_report(base: str, head: str) -> ResyncGuardReport:
    return evaluate_changed_paths(
        changed_paths_from_git(base, head),
        base=base,
        head=head,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail closed when post-gate main drift touches HaxLab "
            "evaluation-owned paths."
        )
    )
    parser.add_argument(
        "--base",
        required=True,
        help="Prepared evaluation staging exact commit SHA.",
    )
    parser.add_argument(
        "--head",
        required=True,
        help="Current main exact commit SHA to compare against base.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(args.base, args.head)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"safe": False, "error": str(exc)}, sort_keys=True))
        return 2

    payload = asdict(report)
    payload["safe"] = report.safe
    print(json.dumps(payload, sort_keys=True))
    return 0 if report.safe else 3


if __name__ == "__main__":
    raise SystemExit(main())
