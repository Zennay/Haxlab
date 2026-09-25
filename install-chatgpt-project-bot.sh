#!/usr/bin/env bash
set -euo pipefail

RAW_BASE="https://raw.githubusercontent.com/Zennay/Haxlab/main/tools/chatgpt-project-bot"
DEST="${CHATGPT_BOT_DIR:-$HOME/.local/share/chatgpt-project-bot}"
mkdir -p "$DEST"
chmod 700 "$DEST"

echo '[1/5] Bot downloaden...'
curl -fsSL "$RAW_BASE/bot.py" -o "$DEST/bot.py"
chmod +x "$DEST/bot.py"

cat > "$DEST/run.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/bot.py" "$@"
RUN
chmod +x "$DEST/run.sh"

curl -fsSL "$RAW_BASE/login.sh" -o "$DEST/login.sh"
chmod +x "$DEST/login.sh"

echo '[2/5] Python + Playwright installeren...'
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install playwright
"$DEST/.venv/bin/playwright" install chromium

if [ ! -f "$DEST/config.json" ]; then
  CHAT_URL="${CHATGPT_CHAT_URL:-}"
  if [ -z "$CHAT_URL" ] && [ -r /dev/tty ]; then
    printf 'ChatGPT project-chat URL: ' > /dev/tty
    IFS= read -r CHAT_URL < /dev/tty || true
  fi
  PROFILE="$DEST/browser-profile"
  python3 - "$DEST/config.json" "$CHAT_URL" "$PROFILE" <<'PY'
import json, sys
path, url, profile = sys.argv[1:]
json.dump({
  "chat_url": url or "https://chatgpt.com/",
  "prompt": "Ga verder met het project. Bekijk eerst de huidige status, kies zelfstandig de volgende logische stap en voer die uit.",
  "profile_dir": profile,
  "headless": True,
  "page_timeout_seconds": 45,
  "post_send_wait_seconds": 3,
  "force_after_minutes": 20,
  "check_interval_seconds": 60,
  "require_high_reasoning": True
}, open(path, "w"), indent=2)
PY
fi

echo '[3/5] systemd daemon instellen...'
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/chatgpt-project-bot.service" <<EOF
[Unit]
Description=ChatGPT project continuation watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart=$DEST/run.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user disable --now chatgpt-project-bot.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/chatgpt-project-bot.timer"
systemctl --user daemon-reload
systemctl --user enable chatgpt-project-bot.service
systemctl --user stop chatgpt-project-bot.service 2>/dev/null || true

echo '[4/5] Watchdog-service geïnstalleerd (start na eenmalige login).'
echo '[5/5] Installatie klaar.'
echo
echo "Eenmalig inloggen:"
echo "  $DEST/login.sh"
echo
echo 'Als je via SSH werkt: verbind eerst met NoMachine zodat er een actieve GUI-sessie is.'
echo 'login.sh probeert die DISPLAY/XAUTHORITY automatisch te vinden.'
echo
echo 'Na SSH logout/reboot actief houden:'
echo '  sudo loginctl enable-linger "$USER"'
echo
echo 'Live logs:'
echo '  journalctl --user -u chatgpt-project-bot.service -f'
