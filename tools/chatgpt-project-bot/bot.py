#!/usr/bin/env python3
import argparse
import fcntl
import json
import logging
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
LOG_PATH = ROOT / "bot.log"
LOCK_PATH = Path("/tmp/chatgpt-project-bot.lock")

DEFAULT_CONFIG = {
    "chat_url": "https://chatgpt.com/",
    "prompt": "Ga verder met het project. Bekijk eerst de huidige status, kies zelfstandig de volgende logische stap en voer die uit.",
    "profile_dir": str(ROOT / "browser-profile"),
    "headless": True,
    "page_timeout_seconds": 45,
    "post_send_wait_seconds": 3,
    "force_after_minutes": 20,
    "check_interval_seconds": 60,
    "require_high_reasoning": True,
}


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )


def load_json(path, fallback):
    if not path.exists():
        return fallback.copy()
    try:
        return fallback | json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Kon %s niet lezen: %s", path.name, exc)
        return fallback.copy()


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def acquire_lock():
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("Een vorige check draait nog; overslaan.")
        raise SystemExit(0)
    return fh


def composer(page):
    return page.locator(
        '#prompt-textarea, [data-testid="prompt-textarea"], textarea, div[contenteditable="true"]'
    ).last


def is_generating(page):
    selectors = [
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop"]',
        'button:has-text("Stop generating")',
        'button:has-text("Stop met genereren")',
    ]
    for sel in selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=350):
                return True
        except Exception:
            pass
    return False


def stop_generation(page):
    selectors = [
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop"]',
        'button:has-text("Stop generating")',
        'button:has-text("Stop met genereren")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click()
                page.wait_for_timeout(1500)
                logging.warning("20-min watchdog: lopende generatie gestopt.")
                return True
        except Exception:
            pass
    logging.warning("20-min watchdog wilde stoppen, maar vond geen stopknop.")
    return False


def ensure_logged_in(page):
    try:
        composer(page).wait_for(state="visible", timeout=12000)
        return True
    except Exception:
        logging.error("Geen composer gevonden. Log éénmalig in met: chatgpt-project-bot --login")
        return False


def visible_exact_text(page, values):
    for value in values:
        try:
            if page.get_by_text(value, exact=True).first.is_visible(timeout=250):
                return value
        except Exception:
            pass
    return None


def ensure_high_reasoning(page):
    """Best effort: keep regular Chat on High reasoning, never intentionally select Work."""
    current_high = visible_exact_text(page, ["High", "Hoog"])
    if current_high:
        logging.info("Reasoning staat op %s.", current_high)
        return True

    picker_labels = [
        "Instant", "Medium", "Gemiddeld", "Extra High", "Zeer Hoog",
        "Pro Standard", "Pro Extended", "Pro standaard", "Pro uitgebreid",
    ]
    picker = None
    for label in picker_labels:
        try:
            loc = page.get_by_text(label, exact=True)
            for i in range(min(loc.count(), 4)):
                candidate = loc.nth(i)
                if candidate.is_visible(timeout=150):
                    picker = candidate
                    break
            if picker:
                break
        except Exception:
            pass

    if picker is None:
        for sel in [
            'button[data-testid*="model"]',
            'button[aria-label*="model" i]',
            'button[aria-label*="reason" i]',
            'button[aria-label*="thinking" i]',
        ]:
            try:
                candidate = page.locator(sel).first
                if candidate.is_visible(timeout=250):
                    picker = candidate
                    break
            except Exception:
                pass

    if picker is None:
        logging.warning("Kon reasoning-kiezer niet vinden. Laat de chat éénmalig handmatig op High staan.")
        return False

    try:
        picker.click()
        page.wait_for_timeout(400)
    except Exception:
        logging.warning("Kon reasoning-kiezer niet openen.")
        return False

    for label in ["High", "Hoog"]:
        try:
            option = page.get_by_text(label, exact=True).last
            if option.is_visible(timeout=700):
                option.click()
                page.wait_for_timeout(500)
                logging.info("Reasoning op %s gezet.", label)
                return True
        except Exception:
            pass

    logging.warning("High-optie niet gevonden. Controleer je account/modelkeuze éénmalig handmatig.")
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def fill_and_send(page, prompt):
    box = composer(page)
    box.wait_for(state="visible", timeout=15000)
    box.click()
    try:
        box.fill(prompt)
    except Exception:
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt)
    page.wait_for_timeout(300)

    for sel in [
        'button[data-testid="send-button"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="Verstuur"]',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=300) and btn.is_enabled():
                btn.click()
                return
        except Exception:
            pass
    box.press("Enter")


def send_prompt(page, cfg, state, forced=False):
    high_ok = ensure_high_reasoning(page)
    if cfg.get("require_high_reasoning", True) and not high_ok:
        logging.warning("High kon niet worden bevestigd; prompt wordt toch verstuurd met de bestaande Chat-instelling.")

    fill_and_send(page, cfg["prompt"])
    now = time.time()
    state["last_send_at"] = now
    state["busy_since"] = now
    state["send_count"] = int(state.get("send_count", 0)) + 1
    if forced:
        state["forced_restart_count"] = int(state.get("forced_restart_count", 0)) + 1
    save_state(state)
    page.wait_for_timeout(int(cfg.get("post_send_wait_seconds", 3)) * 1000)
    logging.info("Vervolgprompt verstuurd%s.", " na watchdog-stop" if forced else "")


def inspect_and_continue(page, cfg, state):
    if not ensure_logged_in(page):
        raise RuntimeError("ChatGPT composer niet beschikbaar; login opnieuw vereist")

    now = time.time()
    busy = is_generating(page)
    if busy:
        busy_since = float(state.get("busy_since") or 0)
        if not busy_since:
            busy_since = now
            state["busy_since"] = busy_since
            save_state(state)

        elapsed = now - busy_since
        force_after = float(cfg.get("force_after_minutes", 20)) * 60
        if elapsed < force_after:
            logging.info(
                "Nog bezig: %.1f min. Opnieuw controleren over ~%d sec; watchdog bij %.0f min.",
                elapsed / 60,
                int(cfg.get("check_interval_seconds", 60)),
                force_after / 60,
            )
            return

        logging.warning("Al %.1f min bezig; watchdog forceert een nieuwe ronde.", elapsed / 60)
        stop_generation(page)
        page.wait_for_timeout(1000)
        send_prompt(page, cfg, state, forced=True)
        return

    state["busy_since"] = 0
    save_state(state)
    logging.info("ChatGPT is klaar; volgende projectstap starten.")
    send_prompt(page, cfg, state, forced=False)


def normal_run(cfg, once=False):
    """Keep one browser alive so closing Chromium cannot interrupt a long generation."""
    profile = Path(os.path.expanduser(cfg["profile_dir"]))
    profile.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {
        "last_send_at": 0,
        "busy_since": 0,
        "send_count": 0,
        "forced_restart_count": 0,
    })
    check_interval = max(10, int(cfg.get("check_interval_seconds", 60)))

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=bool(cfg.get("headless", True)),
            args=["--disable-dev-shm-usage"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            timeout_ms = int(cfg.get("page_timeout_seconds", 45)) * 1000
            page.set_default_timeout(timeout_ms)
            page.goto(cfg["chat_url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            while True:
                try:
                    inspect_and_continue(page, cfg, state)
                except Exception as exc:
                    logging.exception("Check mislukt: %s", exc)
                    if once:
                        raise
                    try:
                        page.goto(cfg["chat_url"], wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(2500)
                    except Exception:
                        raise

                if once:
                    return 0
                time.sleep(check_interval)
        finally:
            ctx.close()


def login_mode(cfg):
    profile = Path(os.path.expanduser(cfg["profile_dir"]))
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(profile), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg.get("chat_url") or "https://chatgpt.com/", wait_until="domcontentloaded")
        print("\nLog in op ChatGPT. Open de projectchat in gewone Chat (niet Work) en zet reasoning op High.")
        print("Druk daarna hier op ENTER om de sessie op te slaan.")
        input()
        ctx.close()
    return 0


def main():
    setup_logging()
    acquire_lock()
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true", help="Voer één inspectie uit en stop.")
    args = parser.parse_args()

    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    if args.status:
        state = load_json(STATE_PATH, {})
        print(json.dumps(state, indent=2))
        return 0
    if args.login:
        return login_mode(cfg)
    return normal_run(cfg, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
