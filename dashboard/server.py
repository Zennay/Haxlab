"""Read-only local HaxLab replay analytics dashboard (no write APIs)."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

DEFAULT_DB = Path("/var/lib/haxlab/state/haxlab.sqlite3")
DEFAULT_DERIVED = Path("/var/lib/haxlab/derived/state-pass-v4")
VERSION = "state-pass-v4"
HERE = Path(__file__).resolve().parent
_player_cache: tuple[float, list[dict]] = (0.0, [])
_player_lock = threading.Lock()


def connect_read_only(path: Path) -> sqlite3.Connection:
    """SQLite URI mode=ro: missing DBs are never implicitly created."""
    conn = sqlite3.connect("file:" + quote(str(path.resolve()), safe="/") + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def overview(conn: sqlite3.Connection) -> dict:
    def count(table: str) -> int:
        # Fixed, internal table names only.
        return int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])

    raw = count("raw_replays")
    proc = dict(conn.execute("SELECT status,COUNT(*) FROM replay_processing GROUP BY status").fetchall())
    analyses = dict(conn.execute(
        "SELECT status,COUNT(*) FROM replay_analysis_versions WHERE analyzer_version=? GROUP BY status", (VERSION,)
    ).fetchall())
    totals = conn.execute(
        """SELECT COALESCE(SUM(sampled_state_count),0), COALESCE(SUM(tick_count),0),
                  COALESCE(SUM(raw_event_count),0)
           FROM replay_analysis_versions WHERE analyzer_version=? AND status='ok'""", (VERSION,)
    ).fetchone()
    daily = conn.execute(
        """SELECT strftime('%Y-%m-%dT%H:00:00Z',updated_at) hour,COUNT(*) total
           FROM replay_analysis_versions
           WHERE analyzer_version=? AND status='ok' AND updated_at>=datetime('now','-23 hours')
           GROUP BY hour""", (VERSION,)
    ).fetchall()
    counts = {row["hour"]: int(row["total"]) for row in daily}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    activity = [{"hour": (now-timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z"),
                 "count": counts.get((now-timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z"), 0)}
                for h in range(23, -1, -1)]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_version": VERSION,
        "raw_replays": raw,
        "processing": {"ok": int(proc.get("ok",0)), "failed": int(proc.get("failed",0)), "pending": max(0,raw-sum(proc.values()))},
        "analysis": {"ok": int(analyses.get("ok",0)), "failed": int(analyses.get("failed",0)),
                     "pending": max(0,int(proc.get("ok",0))-sum(analyses.values()))},
        "sampled_states": int(totals[0]),
        "ticks": int(totals[1]),
        "events": int(totals[2]),
        "activity": activity,
    }


def replays(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT r.sha256, p.status AS processing_status,
                  a.status AS analysis_status, a.player_count, a.sampled_state_count,
                  a.tick_count,a.raw_event_count,a.updated_at
           FROM raw_replays r
           LEFT JOIN replay_processing p ON p.sha256=r.sha256
           LEFT JOIN replay_analysis_versions a ON a.sha256=r.sha256 AND a.analyzer_version=?
           ORDER BY COALESCE(a.updated_at,p.updated_at,r.first_archived_at) DESC LIMIT 80""",
        (VERSION,),
    ).fetchall()
    return [{"id": str(row["sha256"])[:12], "processing": row["processing_status"] or "pending",
             "status": row["analysis_status"] or "pending",
             "players": row["player_count"], "states": row["sampled_state_count"],
             "ticks": row["tick_count"], "events": row["raw_event_count"],
             "updated_at": row["updated_at"]} for row in rows]


def players(root: Path) -> list[dict]:
    global _player_cache
    with _player_lock:
        if time.monotonic() - _player_cache[0] < 120:
            return _player_cache[1]
        # Import is pure stdlib; reuse the canonical HaxLab metric calculations.
        from haxlab.runtime.player_stats import add_proxy_score, collect
        rows = collect(root) if root.is_dir() else []
        rows = [r for r in rows if r["matches"] >= 5 and r["active_minutes"] >= 5]
        add_proxy_score(rows)
        rows.sort(key=lambda r: (r["matches"], r["active_minutes"]), reverse=True)
        safe_fields = ("name", "matches", "active_minutes", "touches_per_min",
                       "touch_retention_pct", "pressured_retention_pct",
                       "recoveries_per_min", "goal_contrib_per_10",
                       "kicks_per_min", "touch_progression", "involvement_score")
        result = [{k: row[k] for k in safe_fields} for row in rows[:150]]
        _player_cache = (time.monotonic(), result)
        return result


def handler_for(db: Path, derived: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                return self.respond(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path not in ("/api/overview", "/api/replays", "/api/players", "/healthz"):
                return self.respond(404, b'{"error":"not_found"}', "application/json")
            if path == "/healthz":
                try:
                    with connect_read_only(db) as conn:
                        conn.execute("SELECT 1 FROM raw_replays LIMIT 1")
                    return self.json_response({"ok": True})
                except (sqlite3.Error, OSError):
                    return self.json_response({"ok": False}, 503)
            try:
                if path == "/api/players":
                    payload = players(derived)
                else:
                    with connect_read_only(db) as conn:
                        payload = overview(conn) if path == "/api/overview" else replays(conn)
                return self.json_response(payload)
            except (sqlite3.Error, OSError, ValueError) as exc:
                self.log_error("dashboard data unavailable: %s", type(exc).__name__)
                return self.json_response({"error":"data_unavailable"}, 503)

        def json_response(self, payload, code=200):
            return self.respond(code, json.dumps(payload, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def respond(self, code, payload, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback-only by default; put authenticated reverse proxy in front")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--derived", type=Path, default=DEFAULT_DERIVED)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(args.db, args.derived))
    print("HaxLab dashboard listening on http://%s:%s" % (args.host,args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
