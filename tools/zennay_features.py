from pathlib import Path
from datetime import datetime, timezone
import json
import re
import sqlite3
import time

HAX_TRAINING = Path("/var/lib/haxlab/derived/training")
HAX_LIVE = Path("/var/lib/haxlab/derived/champions/elite-player/live.json")
FTMO_OUTCOMES = Path("/opt/ftmo-runner/_work/Ftmo/Ftmo/artifacts/research_outcomes")


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {} if default is None else default


def load_resource_policy(root):
    default = {
        "haxlab": {"priority": "low", "cpu_weight": 50, "memory_soft_gb": 2.0, "note": "Background: blijft doorwerken bij contention."},
        "ftmo": {"priority": "high", "cpu_weight": 500, "memory_soft_gb": 5.0, "note": "Voorrang voor actieve research/backtests."},
        "supa": {"priority": "normal", "cpu_weight": 150, "memory_soft_gb": None, "note": "Geen zware compute-service actief."},
        "cloud": {"priority": "light", "cpu_weight": 120, "memory_soft_gb": 0.5, "note": "Control room; licht en responsief."},
    }
    raw = _json(Path(root) / "resource-policy.json", {})
    return {**default, **raw}


def _generation_number(path):
    m = re.search(r"alpha_(\d+)", str(path))
    return int(m.group(1)) if m else 0


def _find_selected_trial(folder, selected_variant):
    for p in folder.glob("trials/*.json"):
        d = _json(p, {})
        result = d.get("result") or {}
        if selected_variant and (d.get("variant") == selected_variant or result.get("variant") == selected_variant):
            return result
    return {}


def _pips(value):
    try:
        return round(float(value) * 10000, 1)
    except Exception:
        return None


def _hax_quality():
    result = {}
    pipeline = None
    candidates = sorted(HAX_TRAINING.glob("*/pipeline-summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Prefer a substantial trained pipeline over PR/smoke artifacts.
    for p in candidates:
        d = _json(p, {})
        frozen = d.get("frozen_holdout") or {}
        if frozen.get("samples", 0) >= 100000:
            pipeline = d
            break
    if pipeline:
        frozen = pipeline.get("frozen_holdout") or {}
        gate = pipeline.get("live_test_gate") or {}
        result.update({
            "headline": f"AI action imitation {float(frozen.get('joint_accuracy', 0))*100:.1f}%",
            "joint_accuracy_pct": round(float(frozen.get("joint_accuracy", 0)) * 100, 1),
            "direction_accuracy_pct": round(float(frozen.get("direction_accuracy", 0)) * 100, 1),
            "kick_f1_pct": round(float(frozen.get("kick_f1", 0)) * 100, 1),
            "holdout_samples": int(frozen.get("samples", 0)),
            "live_test_eligible": bool(gate.get("eligible_for_live_test")),
            "training_replays": int((pipeline.get("selection") or {}).get("selected_replays", 0)),
        })
    live = _json(HAX_LIVE, {})
    if live:
        result.update({
            "live_version": live.get("version_id"),
            "live_healthy": bool((live.get("live_health") or {}).get("healthy")),
            "live_activated_at": live.get("live_activated_at"),
        })
    return result


def _ftmo_quality():
    result = {}
    reviews = sorted(FTMO_OUTCOMES.glob("*/development-review-summary.json"), key=_generation_number, reverse=True)
    if reviews:
        review_path = reviews[0]
        review = _json(review_path, {})
        generation = _generation_number(review_path)
        result["current_generation"] = generation
        result["current_stage"] = "development"
        for row in review.get("reviews", []):
            if row.get("outcome") != "candidate":
                continue
            exp = row.get("experiment_id")
            variant = row.get("selected_variant")
            trial = _find_selected_trial(review_path.parent / str(exp), variant)
            if trial:
                result["current_candidate"] = {
                    "experiment_id": exp,
                    "family": row.get("family"),
                    "stage": "development candidate · not yet walk-forward validated",
                    "closed_trades": int(trial.get("closed_trades", 0)),
                    "win_rate_pct": round(float(trial.get("win_rate", 0)) * 100, 1),
                    "pnl_pips": _pips(trial.get("total_pnl")),
                    "pnl_1_5x_pips": _pips(trial.get("cost_1_5x_pnl")),
                    "without_best_trade_pips": _pips(trial.get("without_best_trade_pnl")),
                }
                result["headline"] = f"G{generation} candidate +{result['current_candidate']['pnl_pips']:.1f} pips development"
            break

    validated = []
    for p in FTMO_OUTCOMES.glob("*/final-holdout-summary.json"):
        d = _json(p, {})
        for row in d.get("decisions", []):
            if row.get("holdout_outcome") != "pass":
                continue
            exp = row.get("experiment_id")
            run = _json(p.parent / str(exp) / "holdout-run.json", {})
            merged = {**row, **(run.get("result") or {})}
            pnl = _pips(merged.get("total_pnl"))
            if pnl is not None:
                validated.append((_generation_number(p), pnl, merged))
    if validated:
        generation, pnl, row = max(validated, key=lambda x: (x[0], x[1]))
        result["validated_holdout"] = {
            "generation": generation,
            "experiment_id": row.get("experiment_id"),
            "closed_trades": int(row.get("closed_trades", 0)),
            "win_rate_pct": round(float(row.get("win_rate", 0)) * 100, 1) if row.get("win_rate") is not None else None,
            "pnl_pips": pnl,
            "pnl_1_5x_pips": _pips(row.get("cost_1_5x_pnl")),
            "without_best_trade_pips": _pips(row.get("without_best_trade_pnl")),
            "status": "frozen holdout passed",
        }
    return result


def collect_quality():
    out = {}
    try:
        out["haxlab"] = _hax_quality()
    except Exception:
        out["haxlab"] = {}
    try:
        out["ftmo"] = _ftmo_quality()
    except Exception:
        out["ftmo"] = {}
    return out


def important_alerts(db_path, limit=12):
    with sqlite3.connect(db_path, timeout=3) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id,ts,project,kind,title,detail FROM events WHERE kind IN ('alert','breakthrough') ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _event(db_path, event_id, project, kind, title, detail):
    with sqlite3.connect(db_path, timeout=3) as c:
        c.execute(
            "INSERT OR IGNORE INTO events(id,ts,project,kind,title,detail) VALUES(?,?,?,?,?,?)",
            (event_id, datetime.now(timezone.utc).isoformat(), project, kind, title, detail),
        )


def evaluate_important_events(root, db_path, data, runner):
    state_path = Path(root) / "alert-state.json"
    state = _json(state_path, {"bad_since": {}, "quality": {}, "milestones": {}, "initialized": False})
    now_ts = time.time()
    visible = [p for p in data.get("projects", []) if p.get("status") != "archived"]

    # Sustained service/project failure: only alert after 15 minutes.
    bad_since = state.setdefault("bad_since", {})
    for p in visible:
        key = "health:" + p["id"]
        if p.get("health") != "healthy":
            bad_since.setdefault(key, now_ts)
            if now_ts - float(bad_since[key]) >= 900:
                _event(db_path, "alert:" + key + ":" + str(int(float(bad_since[key]))), p["id"], "alert",
                       f"{p['name']} heeft aandacht nodig",
                       "Een relevante service is minimaal 15 minuten niet gezond.")
        else:
            bad_since.pop(key, None)

    # Global ChatGPT repeater freeze: stale/offline for 10 minutes.
    rkey = "runner:offline"
    if runner.get("state") in ("stale", "offline"):
        bad_since.setdefault(rkey, now_ts)
        if now_ts - float(bad_since[rkey]) >= 600:
            _event(db_path, "alert:" + rkey + ":" + str(int(float(bad_since[rkey]))), "system", "alert",
                   "ChatGPT project runner lijkt vastgelopen",
                   "De repeater-heartbeat is minimaal 10 minuten stale/offline.")
    else:
        bad_since.pop(rkey, None)

    # Explicit user-input signals can alert immediately.
    text = " ".join(str(x or "") for x in [
        runner.get("event"), (runner.get("last_event") or {}).get("reason"), runner.get("error"), runner.get("title")
    ]).lower()
    if any(k in text for k in ("input required", "needs input", "user action", "approval required", "login required", "captcha")):
        fingerprint = re.sub(r"[^a-z0-9]+", "-", text)[:80]
        _event(db_path, "alert:input:" + fingerprint, "system", "alert",
               "Input nodig", "Een actieve projectrun vraagt om handmatige input.")

    current_quality = {p["id"]: p.get("quality") or {} for p in visible}
    previous_quality = state.setdefault("quality", {})
    if state.get("initialized"):
        old_h = previous_quality.get("haxlab") or {}
        new_h = current_quality.get("haxlab") or {}
        if new_h.get("live_version") and new_h.get("live_version") != old_h.get("live_version"):
            _event(db_path, "breakthrough:hax-live:" + str(new_h["live_version"]), "haxlab", "breakthrough",
                   "HaxLab heeft een nieuwe live champion",
                   f"Nieuwe champion {new_h['live_version']} is live geactiveerd.")
        try:
            delta = float(new_h.get("joint_accuracy_pct", 0)) - float(old_h.get("joint_accuracy_pct", 0))
            if delta >= 3.0:
                _event(db_path, "breakthrough:hax-quality:" + str(new_h.get("joint_accuracy_pct")), "haxlab", "breakthrough",
                       "HaxLab maakte een duidelijke kwaliteitssprong",
                       f"Joint action accuracy steeg met {delta:.1f} procentpunt naar {new_h['joint_accuracy_pct']:.1f}%.")
        except Exception:
            pass

        old_f = previous_quality.get("ftmo") or {}
        new_f = current_quality.get("ftmo") or {}
        old_v = (old_f.get("validated_holdout") or {}).get("experiment_id")
        new_v = (new_f.get("validated_holdout") or {}).get("experiment_id")
        if new_v and new_v != old_v:
            vh = new_f.get("validated_holdout") or {}
            _event(db_path, "breakthrough:ftmo-holdout:" + str(new_v), "ftmo", "breakthrough",
                   "FTMO heeft een nieuwe frozen-holdout pass",
                   f"{new_v} passeerde de frozen holdout met {vh.get('pnl_pips')} pips.")

        old_c = (old_f.get("current_candidate") or {}).get("experiment_id")
        new_c = (new_f.get("current_candidate") or {}).get("experiment_id")
        if new_c and new_c != old_c:
            cc = new_f.get("current_candidate") or {}
            _event(db_path, "breakthrough:ftmo-candidate:" + str(new_c), "ftmo", "breakthrough",
                   "FTMO heeft een nieuwe research-candidate",
                   f"{new_c}: {cc.get('pnl_pips')} pips development; walk-forward validatie volgt.")

        old_m = state.setdefault("milestones", {})
        for p in visible:
            for idx, m in enumerate(p.get("milestones", [])):
                key = f"{p['id']}:{idx}"
                cur = int(m.get("progress", 100 if m.get("done") else 0))
                prev = int(old_m.get(key, cur))
                if prev < 100 <= cur:
                    _event(db_path, "breakthrough:milestone:" + key + ":" + str(cur), p["id"], "breakthrough",
                           f"{p['name']} milestone afgerond", m.get("title", "Milestone afgerond"))
                old_m[key] = cur
    else:
        state["milestones"] = {
            f"{p['id']}:{idx}": int(m.get("progress", 100 if m.get("done") else 0))
            for p in visible for idx, m in enumerate(p.get("milestones", []))
        }

    state["quality"] = current_quality
    state["initialized"] = True
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
