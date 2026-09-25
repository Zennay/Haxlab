#!/usr/bin/env bash
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Deze installer vereist Linux.' >&2; exit 1; }
[[ "$EUID" != 0 ]] || { echo 'Start als desktopgebruiker, zonder sudo; apt vraagt zelf sudo.' >&2; exit 1; }
RAW_BASE="https://raw.githubusercontent.com/Zennay/Haxlab/main/tools/chatgpt-project-bot"
DEST="${CHATGPT_BOT_DIR:-$HOME/.local/share/chatgpt-project-bot}"
# Paths are embedded in systemd syntax; reject ambiguous characters.
[[ "$DEST" == /* && "$DEST" != *[[:space:]\%\"\\]* ]] || { echo 'Gebruik een absoluut pad zonder spaties, %, quotes of backslashes.' >&2; exit 1; }
command -v apt-get >/dev/null || { echo 'Installer ondersteunt Debian/Ubuntu (apt-get).' >&2; exit 1; }
sudo apt-get update
sudo apt-get install -y python3 python3-gi gir1.2-atspi-2.0 at-spi2-core curl
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
for file in bot.py login.sh README.md; do
  curl -fsSL "$RAW_BASE/$file" -o "$STAGE/$file"
done
python3 - "$STAGE/bot.py" <<'PY'
import ast, sys
ast.parse(open(sys.argv[1]).read())
PY
bash -n "$STAGE/login.sh"
mkdir -p "$DEST" "$HOME/.config/systemd/user"
chmod 700 "$DEST"
# Stop the legacy daemon/timer before replacing any files.
systemctl --user disable --now chatgpt-project-bot.timer 2>/dev/null || true
systemctl --user stop chatgpt-project-bot.service 2>/dev/null || true
for file in bot.py login.sh README.md; do install -m 600 "$STAGE/$file" "$DEST/$file"; done
chmod 700 "$DEST/login.sh"
cat > "$DEST/run.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 "$ROOT/bot.py" "$@"
RUN
chmod 700 "$DEST/run.sh"
CHAT_URL="${CHATGPT_CHAT_URL:-}"
if [[ ! -f "$DEST/config.json" && -z "$CHAT_URL" && -r /dev/tty ]]; then
  printf 'Exacte ChatGPT chat-URL (https://chatgpt.com/c/...): ' > /dev/tty
  IFS= read -r CHAT_URL < /dev/tty
fi
python3 - "$DEST" "$CHAT_URL" <<'PY'
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('bot', root / 'bot.py')
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
path = root / 'config.json'
old = json.loads(path.read_text()) if path.exists() else {}
if path.exists():
    (root / 'config.before-desktop.json').write_text(path.read_text())
# Keep URL, prompt and native label customizations; retire browser-launch settings.
cfg = {k: old.get(k, v) for k, v in bot.DEFAULT.items()}
if sys.argv[2]:
    cfg['chat_url'] = bot.canonical(sys.argv[2])
cfg.update(check_interval_seconds=60, force_after_minutes=20)
bot.save(path, cfg)
PY
cat > "$HOME/.config/systemd/user/chatgpt-project-bot.service" <<EOF
[Unit]
Description=ChatGPT native AT-SPI desktop watchdog
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart="$DEST/run.sh"
Restart=on-failure
RestartSec=30
UMask=0077
Environment=PYTHONUNBUFFERED=1
EOF
systemctl --user daemon-reload
# Explicit desktop attachment is required after each install/login; no headless autostart.
systemctl --user disable chatgpt-project-bot.service 2>/dev/null || true
printf '\nGeïnstalleerd. Stel chat_url in via %s/config.json.\n' "$DEST"
printf 'Open zelf je browser en voer in de desktopterminal uit: %s/login.sh\n' "$DEST"
echo 'Logs: journalctl --user -u chatgpt-project-bot.service -f'
