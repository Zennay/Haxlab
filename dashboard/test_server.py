"""Read-only dashboard regression tests, independent of VPS replay data."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import URLError


SERVER = Path(__file__).with_name("server.py")
spec = importlib.util.spec_from_file_location("haxlab_dashboard_server", SERVER)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.sqlite3"
        self.derived = Path(self.tmp.name) / "empty-derived"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
            CREATE TABLE raw_replays(sha256 TEXT PRIMARY KEY,first_archived_at TEXT);
            CREATE TABLE replay_processing(sha256 TEXT,status TEXT,updated_at TEXT);
            CREATE TABLE replay_analysis_versions(
              sha256 TEXT,analyzer_version TEXT,status TEXT,player_count INTEGER,
              sampled_state_count INTEGER,tick_count INTEGER,raw_event_count INTEGER,updated_at TEXT);
            INSERT INTO raw_replays VALUES ('abcdef1234567890','2026-10-12 20:00:00');
            INSERT INTO replay_processing VALUES ('abcdef1234567890','ok','2026-10-12 20:00:00');
            INSERT INTO replay_analysis_versions VALUES
              ('abcdef1234567890','state-pass-v4','ok',4,200,1200,63,'2026-10-12 20:01:00');
            """)

    def test_read_only_connection_and_aggregates(self):
        with dashboard.connect_read_only(self.db) as conn:
            result = dashboard.overview(conn)
            rows = dashboard.replays(conn)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM raw_replays")
        self.assertEqual(result["raw_replays"], 1)
        self.assertEqual(result["analysis"]["ok"], 1)
        self.assertEqual(result["sampled_states"], 200)
        self.assertEqual(result["events"], 63)
        self.assertEqual(rows[0]["id"], "abcdef123456")
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["players"], 4)
        self.assertEqual(len(result["activity"]), 24)

    def test_missing_database_never_created(self):
        absent = Path(self.tmp.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            dashboard.connect_read_only(absent)
        self.assertFalse(absent.exists())

    def test_http_routes_and_no_mutation_methods(self):
        web = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.handler_for(self.db, self.derived))
        server_thread = threading.Thread(target=web.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(web.server_close)
        self.addCleanup(web.shutdown)
        host, port = web.server_address
        conn = HTTPConnection(host, port, timeout=3)
        conn.request("GET", "/api/overview")
        r = conn.getresponse()
        payload = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(payload["analysis"]["ok"], 1)
        conn.request("GET", "/api/players")
        r = conn.getresponse()
        self.assertEqual(json.loads(r.read()), [])
        self.assertEqual(r.status, 200)
        conn.request("GET", "/not-found")
        r = conn.getresponse()
        self.assertEqual(r.status, 404)
        r.read()
        conn.request("POST", "/api/overview")
        r = conn.getresponse()
        self.assertEqual(r.status, 501)
        r.read()
        conn.close()


if __name__ == "__main__":
    unittest.main()
