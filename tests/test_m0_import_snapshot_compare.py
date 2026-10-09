"""Offline regression tests for the read-only M0 snapshot comparator."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import importlib.util

# CI installs only the src/ package. The standalone read-only tool intentionally
# is not installed as a production package, so load it by its repository path.
_TOOL_FILE = Path(__file__).resolve().parents[1] / "tools" / "m0_import_snapshot_compare.py"
_SPEC = importlib.util.spec_from_file_location("m0_import_snapshot_compare", _TOOL_FILE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Could not load M0 comparator spec")
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)
ARTIFACTS = _TOOL.ARTIFACTS
SnapshotError = _TOOL.SnapshotError
compare = _TOOL.compare
main = _TOOL.main
snapshot = _TOOL.snapshot


def _fixture(root: Path) -> None:
    root.mkdir()
    manifest = {
        "schema_version": 1,
        "replay_count": 1,
        "unique_replay_count": 1,
        "duplicate_replay_count": 0,
        "report_count": 1,
        "match_count": 1,
        "unmatched_replays": [],
        "unmatched_reports": [],
        "failures": [],
    }
    for name in ARTIFACTS:
        payload = json.dumps(manifest, sort_keys=True) if name == "manifest.json" else "[]\n"
        (root / name).write_text(payload, encoding="utf-8")


class M0SnapshotComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.before = root / "before"
        self.after = root / "after"
        _fixture(self.before)
        shutil.copytree(self.before, self.after)

    def _change_counts(self, **changes: int) -> None:
        path = self.after / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def test_identical_snapshots_report_all_five_bytes(self) -> None:
        result = compare(self.before, self.after)
        self.assertTrue(result["identical"])
        self.assertEqual(result["changed_artifacts"], [])
        self.assertEqual(result["count_delta"], {})
        self.assertEqual(sorted(result["before"]["sha256"]), sorted(ARTIFACTS))
        self.assertEqual(result["before"], result["after"])

    def test_single_changed_replay_artifact_detected(self) -> None:
        (self.after / "replays.json").write_text("[ ]\n", encoding="utf-8")
        result = compare(self.before, self.after)
        self.assertFalse(result["identical"])
        self.assertEqual(result["changed_artifacts"], ["replays.json"])
        self.assertEqual(result["count_delta"], {})

    def test_manifest_delta_is_signed_and_stable(self) -> None:
        self._change_counts(replay_count=2, unique_replay_count=2)
        result = compare(self.before, self.after)
        self.assertEqual(result["changed_artifacts"], ["manifest.json"])
        self.assertEqual(result["count_delta"], {"replay_count": 1, "unique_replay_count": 1})
        reverse = compare(self.after, self.before)
        self.assertEqual(reverse["count_delta"], {"replay_count": -1, "unique_replay_count": -1})

    def test_missing_artifact_fails_closed(self) -> None:
        (self.after / "duplicates.json").unlink()
        with self.assertRaisesRegex(SnapshotError, "duplicates.json"):
            snapshot(self.after)

    def test_artifact_symlink_rejected(self) -> None:
        source = self.after / "manifest.json"
        destination = self.after / "manifest-target.json"
        source.rename(destination)
        source.symlink_to(destination)
        with self.assertRaisesRegex(SnapshotError, "manifest.json"):
            snapshot(self.after)

    def test_root_symlink_rejected(self) -> None:
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.before, target_is_directory=True)
        with self.assertRaisesRegex(SnapshotError, "real directory"):
            snapshot(link)

    def test_directory_in_place_of_artifact_rejected(self) -> None:
        artifact = self.after / "reports.json"
        artifact.unlink()
        artifact.mkdir()
        with self.assertRaisesRegex(SnapshotError, "reports.json"):
            snapshot(self.after)

    def test_invalid_manifest_count_or_coherence_rejected(self) -> None:
        for bad in (
            {"match_count": True},
            {"match_count": -1},
            {"unique_replay_count": 2},
            {"report_count": 0},
            {"schema_version": 2},
        ):
            with self.subTest(bad=bad):
                _fixture_path = self.after / "manifest.json"
                manifest = json.loads((self.before / "manifest.json").read_text(encoding="utf-8"))
                manifest.update(bad)
                _fixture_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(SnapshotError):
                    snapshot(self.after)

    def test_duplicate_json_keys_and_nonfinite_rejected(self) -> None:
        path = self.after / "manifest.json"
        original = (self.before / "manifest.json").read_text(encoding="utf-8")
        for payload in (
            original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
            original.replace('"match_count": 1', '"match_count": NaN'),
            "{invalid json",
        ):
            with self.subTest(payload=payload[:35]):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(SnapshotError):
                    snapshot(self.after)

    def test_cli_report_only_vs_strict_and_invalid(self) -> None:
        (self.after / "reports.json").write_text("[ ]", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(self.before), str(self.after)]), 0)
        self.assertEqual(json.loads(out.getvalue())["changed_artifacts"], ["reports.json"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.before), str(self.after), "--fail-on-drift"]), 1)
        (self.after / "reports.json").unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.before), str(self.after)]), 2)

    def test_report_is_deterministic(self) -> None:
        self.assertEqual(
            json.dumps(compare(self.before, self.after), sort_keys=True),
            json.dumps(compare(self.before, self.after), sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
