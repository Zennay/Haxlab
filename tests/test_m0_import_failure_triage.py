"""Deterministic, privacy-safe, bounded M0 diagnostic regression tests."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from m0_import_failure_triage import TriageError, main, triage, MAX_MANIFEST_BYTES


def fixture(root: Path, failures=None):
    root.mkdir()
    payload = {
        "schema_version": 1, "replay_count": 5, "unique_replay_count": 4,
        "duplicate_replay_count": 1, "report_count": 2, "match_count": 1,
        "unmatched_replays": ["one", "two"], "unmatched_reports": ["report"],
        "failures": failures if failures is not None else [
            {"source": "/private/secret/channel.json", "stage": "discord_json",
             "error": "secret-token"},
            {"source": "/private/hidden/match.hbr2", "stage": "hbr2_validation",
             "error": "bad bytes"},
            {"source": "third.json", "stage": "future_private_path", "error": "private details"},
        ],
    }
    (root / "manifest.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return payload


class M0FailureTriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "snapshot"
        self.payload = fixture(self.root)

    def save(self, payload):
        (self.root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_reports_only_safe_aggregates_and_is_repeatable(self):
        first = triage(self.root)
        self.assertEqual(first, triage(self.root))
        self.assertEqual(first["failure_records"], 3)
        self.assertEqual(first["by_stage"],
                         {"discord_json": 1, "hbr2_validation": 1, "other_stage": 1})
        self.assertEqual(first["unmatched_valid_replays"], 2)
        self.assertEqual(first["unmatched_reports"], 1)
        self.assertEqual(len(first["manifest_sha256"]), 64)
        rendered = json.dumps(first)
        for secret in ("secret-token", "/private", "private details", "future_private_path"):
            self.assertNotIn(secret, rendered)
        for field in ("raw_inputs_validated", "full_dataset_integrity_proven",
                      "live_runtime_observed", "training_authorized", "promotion_authorized"):
            self.assertFalse(first[field])

    def test_cli_report_only_and_fail_on_failures(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(self.root)]), 0)
        self.assertEqual(json.loads(out.getvalue())["failure_records"], 3)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.root), "--fail-on-failures"]), 1)
        self.save({**self.payload, "failures": []})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.root), "--fail-on-failures"]), 0)

    def test_no_source_writes_or_mtime_changes(self):
        file = self.root / "manifest.json"
        before = (file.read_bytes(), file.stat().st_mtime_ns)
        triage(self.root)
        self.assertEqual(before, (file.read_bytes(), file.stat().st_mtime_ns))

    def test_root_and_manifest_symlinks_denied(self):
        link = self.root.parent / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(TriageError, "unsafe_or_missing_root"):
            triage(link)
        file = self.root / "manifest.json"
        file.rename(self.root / "original.json")
        file.symlink_to(self.root / "original.json")
        with self.assertRaisesRegex(TriageError, "unsafe_or_missing_manifest"):
            triage(self.root)

    def test_missing_and_nonregular_manifest_denied(self):
        file = self.root / "manifest.json"
        file.unlink()
        with self.assertRaisesRegex(TriageError, "unsafe_or_missing_manifest"):
            triage(self.root)
        file.mkdir()
        with self.assertRaisesRegex(TriageError, "non_regular_manifest"):
            triage(self.root)

    def test_unbounded_manifest_denied(self):
        (self.root / "manifest.json").write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(TriageError, "manifest_too_large"):
            triage(self.root)

    def test_duplicate_nonfinite_and_malformed_json_denied(self):
        file = self.root / "manifest.json"
        for raw, reason in ((b'{"schema_version":1,"schema_version":1}', "duplicate_json_key"),
                            (b'{"schema_version": NaN}', "nonfinite_json_constant"),
                            (b'not-json', "invalid_manifest_json")):
            with self.subTest(raw=raw):
                file.write_bytes(raw)
                with self.assertRaisesRegex(TriageError, reason):
                    triage(self.root)

    def test_invalid_counters_denied(self):
        for field, value, reason in (("replay_count", True, "invalid_manifest_count"),
                                     ("match_count", -1, "invalid_manifest_count"),
                                     ("unique_replay_count", 20, "inconsistent_manifest_counts"),
                                     ("schema_version", 2, "invalid_manifest_schema")):
            with self.subTest(field=field):
                self.save({**self.payload, field: value})
                with self.assertRaisesRegex(TriageError, reason):
                    triage(self.root)

    def test_invalid_unmatched_evidence_denied(self):
        for patch in ({"unmatched_reports": []},
                      {"unmatched_replays": ["one", "one"]},
                      {"unmatched_replays": ["one", "two", "three", "four"]},
                      {"unmatched_reports": [" "]}):
            with self.subTest(patch=patch):
                self.save({**self.payload, **patch})
                with self.assertRaises(TriageError):
                    triage(self.root)

    def test_malformed_failure_records_denied_without_raw_error_leak(self):
        for rows in (None, {}, [None], [{"stage": "hbr2_validation", "error": "secret"}],
                     [{"stage": "hbr2_validation ", "source": "a", "error": "bad"}],
                     [{"stage": "x", "source": "a", "error": ""}]):
            with self.subTest(rows=rows):
                self.save({**self.payload, "failures": rows})
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main([str(self.root)]), 2)
                body = json.loads(out.getvalue())
                self.assertFalse(body["promotion_authorized"])
                self.assertNotIn("secret", out.getvalue())

    def test_detects_root_swap_during_read(self):
        import m0_import_failure_triage as module
        original_stat = module.os.stat
        reads = 0

        def swap(name, *args, **kwargs):
            nonlocal reads
            if name == "manifest.json" and kwargs.get("dir_fd") is not None:
                reads += 1
                fake = original_stat(name, *args, **kwargs)
                if reads == 2:
                    values = list(fake)
                    values[1] += 1
                    return type(fake)(values)
                return fake
            return original_stat(name, *args, **kwargs)

        with mock.patch.object(module.os, "stat", side_effect=swap):
            with self.assertRaisesRegex(TriageError, "manifest_or_root_changed"):
                triage(self.root)


if __name__ == "__main__":
    unittest.main()
