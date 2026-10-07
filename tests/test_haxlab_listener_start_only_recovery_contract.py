from __future__ import annotations

import re
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "haxlab-listener-start-only-recovery-20261007.yml"
)


class StartOnlyRecoveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_scoped_zcloud_vps_runner_and_non_cancelling_concurrency(self) -> None:
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_forbids_destructive_listener_control(self) -> None:
        forbidden = re.compile(
            r"^\s*(?:sudo\s+-n\s+)?systemctl\s+(?:restart|stop|kill)\b",
            re.MULTILINE,
        )
        self.assertIsNone(forbidden.search(self.text))
        self.assertNotRegex(self.text, r"^\s*(?:pkill|killall)\b")

    def test_only_start_is_guarded_behind_worker_and_inactive_state_checks(self) -> None:
        start = 'sudo -n systemctl start "$SERVICE"'
        self.assertEqual(self.text.count(start), 1)
        worker_guard = 'if [ "$worker_before" -eq 1 ]; then'
        state_guard = 'inactive|failed)'
        self.assertLess(self.text.index(worker_guard), self.text.index(start))
        self.assertLess(self.text.index(state_guard), self.text.index(start))
        self.assertIn("action=preserve-active-worker", self.text)

    def test_active_listener_path_is_preserve_only(self) -> None:
        active_block = """active)
              action=preserve-active-listener
              ;;"""
        self.assertIn(active_block, self.text)


if __name__ == "__main__":
    unittest.main()
