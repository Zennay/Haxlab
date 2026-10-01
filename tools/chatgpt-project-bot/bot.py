#!/usr/bin/env python3
"""Native AT-SPI controller. Never starts or navigates a browser."""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULT = {
    'chat_url': '',
    'prompt': 'Ga verder met het project. Bekijk eerst de huidige status, kies zelfstandig de volgende logische stap en voer die uit.',
    'check_interval_seconds': 60,
    'force_after_minutes': 20,
    'composer_names': ['Message ChatGPT', 'Bericht aan ChatGPT', 'Ask anything', 'Vraag maar raak'],
    'stop_names': ['Stop generating', 'Stop streaming', 'Stop met genereren', 'Genereren stoppen'],
    'send_names': ['Send prompt', 'Send message', 'Send', 'Bericht verzenden', 'Versturen'],
    'high_names': ['High', 'Hoog'],
}


def read(path, default):
    return default.copy() if not path.exists() else default | json.loads(path.read_text())


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'chatgpt.com' or not p.path.startswith('/c/') or not p.path[3:].strip('/'):
        raise ValueError('Stel een exacte https://chatgpt.com/c/... chat_url in.')
    return 'https://chatgpt.com' + p.path.rstrip('/')


class Desktop:
    def __init__(self, cfg):
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi
        self.a = Atspi
        self.cfg = cfg
        self.url = canonical(cfg['chat_url'])

    def walk(self, root):
        stack, count = [root], 0
        while stack:
            node = stack.pop()
            count += 1
            if count > 30000:
                raise RuntimeError('Accessibility-boom te groot; geen actie.')
            if node is None:
                raise RuntimeError('Accessibility-boom veranderde; wacht op volgende controle.')
            yield node
            stack.extend(node.get_child_at_index(i) for i in reversed(range(node.get_child_count())))

    def showing(self, node):
        s = node.get_state_set()
        return s.contains(self.a.StateType.SHOWING) and s.contains(self.a.StateType.VISIBLE)

    def enabled(self, node):
        return node.get_state_set().contains(self.a.StateType.ENABLED)

    def document(self):
        matches = []
        for node in self.walk(self.a.get_desktop(0)):
            if node.get_role() != self.a.Role.DOCUMENT_WEB or not self.showing(node):
                continue
            doc = node.get_document_iface()
            attrs = doc.get_attributes() if doc else {}
            url = next((v for k, v in attrs.items() if k.lower() in ('docurl', 'url', 'uri')), '')
            try:
                if canonical(url) == self.url:
                    matches.append(node)
            except ValueError:
                pass
        if len(matches) != 1:
            raise RuntimeError('Exact één zichtbare doelchat vereist; controleer URL, tab en accessibility.')
        return matches[0]

    def snapshot(self):
        doc = self.document()
        nodes = [n for n in self.walk(doc) if self.showing(n)]
        names = [(n.get_name() or '').strip() for n in nodes]
        blockers = ('verify you are human', 'checking your browser', 'captcha', 'verifieer dat je een mens',
                    'ben je een robot', 'unusual activity', 'unusual traffic', 'usage limit', 'limiet bereikt',
                    'something went wrong', 'er is iets misgegaan', 'log in', 'inloggen')
        if any(any(b in name.lower() for b in blockers) for name in names):
            raise RuntimeError('Login, robotcheck, fout of limiet zichtbaar; handmatig oplossen.')
        if any(n.get_role() in (self.a.Role.DIALOG, self.a.Role.ALERT) for n in nodes):
            raise RuntimeError('Dialoog/waarschuwing zichtbaar; handmatig controleren.')
        buttons = [n for n in nodes if n.get_role() in (self.a.Role.PUSH_BUTTON, self.a.Role.TOGGLE_BUTTON)]
        def named(key):
            return [n for n in buttons if (n.get_name() or '').strip() in self.cfg[key]]
        high = named('high_names')
        if len(high) != 1:
            raise RuntimeError('High/Hoog niet eenduidig zichtbaar als bediening; stel handmatig in.')
        composers = [n for n in nodes if (n.get_name() or '').strip() in self.cfg['composer_names']
                     and n.get_state_set().contains(self.a.StateType.EDITABLE)]
        if len(composers) != 1:
            raise RuntimeError('Geen unieke herkende berichtinvoer; gebruik --inspect voor labels.')
        box = composers[0]
        text = box.get_text_iface()
        if text is None:
            raise RuntimeError('Berichtinvoer biedt geen AT-SPI Text-interface.')
        stops, sends = named('stop_names'), named('send_names')
        if len(stops) > 1 or len(sends) > 1:
            raise RuntimeError('Dubbele Stop/Send-bediening; geen actie.')
        return {'busy': bool(stops), 'stop': stops[0] if stops else None,
                'send': sends[0] if sends else None, 'box': box,
                'draft': text.get_text(0, -1)}

    def click(self, node):
        if not self.showing(node) or not self.enabled(node):
            raise RuntimeError('Bediening niet zichtbaar/beschikbaar.')
        action = node.get_action_iface()
        if action:
            for i in range(action.get_n_actions()):
                if action.get_action_name(i).lower() in ('click', 'press', 'activate'):
                    if action.do_action(i):
                        return
        raise RuntimeError('AT-SPI-actie mislukt; geen toetsenbordfallback.')

    def stop(self):
        snap = self.snapshot()
        if not snap['busy']:
            return
        self.click(snap['stop'])
        for _ in range(15):
            time.sleep(1)
            if not self.snapshot()['busy']:
                return
        raise RuntimeError('Stop niet bevestigd; geen vervolgprompt.')

    def send(self, prompt):
        snap = self.snapshot()
        if snap['busy'] or snap['draft'].strip():
            raise RuntimeError('Generatie of bestaande concepttekst; niets overschrijven.')
        edit = snap['box'].get_editable_text_iface()
        if edit is None or not edit.set_text_contents(prompt):
            raise RuntimeError('AT-SPI tekstinvoer niet ondersteund.')
        time.sleep(0.5)
        snap = self.snapshot()
        if snap['busy'] or snap['draft'] != prompt or snap['send'] is None:
            raise RuntimeError('Invoer/Send niet bevestigd; concept laten staan.')
        self.click(snap['send'])
        # Only a visible Stop confirms acceptance. Fast/ambiguous responses pause.
        for _ in range(15):
            time.sleep(1)
            if self.snapshot()['busy']:
                return
        raise RuntimeError('Verzending niet bevestigd; gebruik --login na handmatige controle.')

    def inspect(self):
        for node in self.walk(self.document()):
            if self.showing(node) and (node.get_role() in (self.a.Role.PUSH_BUTTON, self.a.Role.TOGGLE_BUTTON)
                                      or node.get_state_set().contains(self.a.StateType.EDITABLE)):
                print(node.get_role_name(), repr(node.get_name()))


class Controller:
    def __init__(self, cfg, state, persist):
        self.cfg, self.state, self.persist = cfg, state, persist

    def tick(self, ui, now):
        s = self.state
        if s.get('pending'):
            raise RuntimeError('Vorige actie onzeker. Controleer chat en voer login.sh opnieuw uit.')
        snap = ui.snapshot()
        if snap['draft'].strip():
            raise RuntimeError('Concept aanwezig; wacht op gebruiker.')
        if snap['busy']:
            if s.get('busy_since') is None:
                s['busy_since'] = now
                self.persist(s)
            if now - s['busy_since'] < self.cfg['force_after_minutes'] * 60:
                logging.info('Nog bezig: %.1f minuten', (now - s['busy_since']) / 60)
                return
            s['pending'] = 'stop'
            self.persist(s)
            ui.stop()
            s['forced_restart_count'] = s.get('forced_restart_count', 0) + 1
        # Persist intent BEFORE any input: a crash cannot cause duplicate sends.
        s['pending'] = 'send'
        self.persist(s)
        ui.send(self.cfg['prompt'])
        s.update(pending=None, busy_since=now, send_count=s.get('send_count', 0) + 1)
        self.persist(s)
        logging.info('Vervolgprompt verstuurd en generatie bevestigd.')


def main():
    p = argparse.ArgumentParser()
    for flag in ('login', 'status', 'inspect', 'once'):
        p.add_argument('--' + flag, action='store_true')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = read(ROOT / 'config.json', DEFAULT)
    cfg['chat_url'] = canonical(cfg['chat_url'])
    if not cfg['prompt'].strip() or cfg['check_interval_seconds'] < 1 or cfg['force_after_minutes'] <= 0:
        raise ValueError('Ongeldige prompt of tijdsinstellingen.')
    state_path = ROOT / 'desktop-state.json'
    state = read(state_path, {})
    if args.status:
        print(json.dumps(state, indent=2))
        return
    lock = (ROOT / 'desktop.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Bot draait al; stop eerst de service.')
    ui = Desktop(cfg)
    if args.inspect:
        ui.inspect()
        return
    if args.login:
        print('Open zelf de browser, log in en open ' + cfg['chat_url'])
        print('Kies gewone Chat en High/Hoog. Laat deze tab zichtbaar en de desktop actief.')
        if input('Typ CHAT HIGH om dit te bevestigen: ').strip() != 'CHAT HIGH':
            raise SystemExit('Niet bevestigd.')
        snap = ui.snapshot()
        if snap['draft'].strip():
            raise SystemExit('Verstuur/verwijder eerst de bestaande concepttekst.')
        save(state_path, {'chat_url': cfg['chat_url'], 'armed': True,
                          'busy_since': time.time() if snap['busy'] else None, 'pending': None})
        return
    if not state.get('armed') or state.get('chat_url') != cfg['chat_url']:
        raise SystemExit('Voer eerst login.sh uit vanuit je grafische desktop.')
    controller = Controller(cfg, state, lambda s: save(state_path, s))
    while True:
        started = time.monotonic()
        try:
            controller.tick(ui, time.time())
        except Exception as exc:
            logging.warning('Gepauzeerd: %s', exc)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(max(0, cfg['check_interval_seconds'] - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
