#!/usr/bin/env bash
set -euo pipefail

ROOT="${CHATGPT_BOT_DIR:-$HOME/.local/share/chatgpt-project-bot}"
SERVICE="chatgpt-project-bot.service"

systemctl --user stop "$SERVICE" 2>/dev/null || true

export_gui_from_pid() {
  local pid="$1" env_file="/proc/$1/environ"
  [ -r "$env_file" ] || return 1

  local d xa db wd
  d="$(tr '\0' '\n' < "$env_file" 2>/dev/null | sed -n 's/^DISPLAY=//p' | head -n1)"
  [ -n "$d" ] || return 1

  xa="$(tr '\0' '\n' < "$env_file" 2>/dev/null | sed -n 's/^XAUTHORITY=//p' | head -n1)"
  db="$(tr '\0' '\n' < "$env_file" 2>/dev/null | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' | head -n1)"
  wd="$(tr '\0' '\n' < "$env_file" 2>/dev/null | sed -n 's/^WAYLAND_DISPLAY=//p' | head -n1)"

  export DISPLAY="$d"
  [ -n "$xa" ] && export XAUTHORITY="$xa"
  [ -n "$db" ] && export DBUS_SESSION_BUS_ADDRESS="$db"
  [ -n "$wd" ] && export WAYLAND_DISPLAY="$wd"
  return 0
}

find_gui() {
  # Explicit override always wins.
  if [ -n "${CHATGPT_BOT_DISPLAY:-}" ]; then
    export DISPLAY="$CHATGPT_BOT_DISPLAY"
    [ -n "${CHATGPT_BOT_XAUTHORITY:-}" ] && export XAUTHORITY="$CHATGPT_BOT_XAUTHORITY"
    echo "Gebruik expliciete DISPLAY=$DISPLAY"
    return 0
  fi

  # Already inside a graphical terminal.
  if [ -n "${DISPLAY:-}" ]; then
    echo "Grafische sessie al actief: DISPLAY=$DISPLAY"
    return 0
  fi

  # Best source: another process from this same user that belongs to the desktop.
  local pid
  while IFS= read -r pid; do
    if export_gui_from_pid "$pid"; then
      echo "Grafische sessie gevonden via proces $pid: DISPLAY=$DISPLAY"
      return 0
    fi
  done < <(ps -u "$USER" -o pid= 2>/dev/null | awk '{print $1}')

  # systemd-logind sometimes exposes the X11 display directly.
  if command -v loginctl >/dev/null 2>&1; then
    local sid user d
    while read -r sid _ user _; do
      [ "$user" = "$USER" ] || continue
      d="$(loginctl show-session "$sid" -p Display --value 2>/dev/null || true)"
      if [ -n "$d" ]; then
        export DISPLAY="$d"
        [ -f "$HOME/.Xauthority" ] && export XAUTHORITY="$HOME/.Xauthority"
        echo "Grafische sessie gevonden via loginctl: DISPLAY=$DISPLAY"
        return 0
      fi
    done < <(loginctl list-sessions --no-legend 2>/dev/null || true)
  fi

  # Last fallback: X11 sockets. This is enough on setups that use ~/.Xauthority.
  local sock n
  for sock in /tmp/.X11-unix/X*; do
    [ -S "$sock" ] || continue
    n="${sock##*/X}"
    export DISPLAY=":$n"
    [ -f "$HOME/.Xauthority" ] && export XAUTHORITY="$HOME/.Xauthority"
    echo "Mogelijke X11-sessie gevonden via $sock: DISPLAY=$DISPLAY"
    return 0
  done

  return 1
}

if ! find_gui; then
  cat >&2 <<'MSG'

Geen actieve GUI/X11-sessie gevonden.

De eerste ChatGPT-login moet één keer in een zichtbare browser gebeuren.
Snelste route:
  1. Open NoMachine naar deze VPS.
  2. Open daar een terminal.
  3. Run:
       ~/.local/share/chatgpt-project-bot/login.sh

Daarna draait de watchdog volledig headless en heb je NoMachine niet meer nodig.

Als NoMachine wel open is maar auto-detectie faalt:
  - run in de NoMachine-terminal: echo $DISPLAY
  - run via SSH bijvoorbeeld:
      CHATGPT_BOT_DISPLAY=:1001 ~/.local/share/chatgpt-project-bot/login.sh
MSG
  exit 2
fi

if [ ! -x "$ROOT/run.sh" ]; then
  echo "Bot niet gevonden in $ROOT. Run eerst de installer." >&2
  exit 3
fi

echo "Browser wordt geopend op DISPLAY=$DISPLAY"
"$ROOT/run.sh" --login

systemctl --user start "$SERVICE"
echo "ChatGPT watchdog gestart."
