import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bot', Path(__file__).resolve().parents[1] / 'bot.py')
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

class UI:
    def __init__(self, busy=False, draft='', fail=None):
        self.busy, self.draft, self.fail = busy, draft, fail
        self.calls = []
    def snapshot(self):
        if self.fail == 'snapshot': raise RuntimeError('captcha/high/url unavailable')
        return dict(busy=self.busy, draft=self.draft)
    def stop(self):
        self.calls.append('stop')
        if self.fail == 'stop': raise RuntimeError('stop still visible')
        self.busy = False
    def send(self, prompt):
        self.calls.append('send')
        if self.fail == 'send': raise RuntimeError('unconfirmed')
        self.busy = True

class Tests(unittest.TestCase):
    def run_tick(self, ui, state, now=1200):
        saved = []
        bot.Controller(bot.DEFAULT, state, lambda s: saved.append(s.copy())).tick(ui, now)
        return saved
    def test_wait_until_twenty_minutes(self):
        ui, s = UI(True), {'busy_since': 1}
        self.run_tick(ui, s)
        self.assertEqual(ui.calls, [])
        self.run_tick(ui, s, 1201)
        self.assertEqual(ui.calls, ['stop', 'send'])
    def test_adopt_generation(self):
        ui, s = UI(True), {}
        self.run_tick(ui, s)
        self.assertEqual(s['busy_since'], 1200)
        self.assertEqual(ui.calls, [])
    def test_finished_sends_and_persists_intent_first(self):
        ui, s = UI(), {'busy_since': 10}
        saved = self.run_tick(ui, s)
        self.assertEqual(ui.calls, ['send'])
        self.assertEqual(saved[0]['pending'], 'send')
        self.assertIsNone(s['pending'])
    def test_failed_stop_never_sends(self):
        ui, s = UI(True, fail='stop'), {'busy_since': 0}
        with self.assertRaises(RuntimeError): self.run_tick(ui, s)
        self.assertEqual(ui.calls, ['stop'])
        self.assertEqual(s['pending'], 'stop')
    def test_unconfirmed_send_not_retried_after_restart(self):
        ui, s = UI(fail='send'), {}
        with self.assertRaises(RuntimeError): self.run_tick(ui, s)
        with self.assertRaises(RuntimeError): self.run_tick(ui, s, 1260)
        self.assertEqual(ui.calls, ['send'])
    def test_draft_preserved(self):
        ui = UI(draft='my draft')
        with self.assertRaises(RuntimeError): self.run_tick(ui, {})
        self.assertEqual(ui.calls, [])
    def test_unsafe_snapshot_never_acts(self):
        ui = UI(fail='snapshot')
        with self.assertRaises(RuntimeError): self.run_tick(ui, {})
        self.assertEqual(ui.calls, [])
    def test_exact_chat_url(self):
        self.assertEqual(bot.canonical('https://chatgpt.com/c/abc/?x=1'), 'https://chatgpt.com/c/abc')
        for url in ('https://chatgpt.com/', 'https://evil.com/c/abc', 'https://chatgpt.com.evil.com/c/abc', 'http://chatgpt.com/c/abc'):
            with self.assertRaises(ValueError): bot.canonical(url)

if __name__ == '__main__': unittest.main()
