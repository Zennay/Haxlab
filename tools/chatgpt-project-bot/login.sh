#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" || -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  echo 'Voer login.sh uit in een terminal in je actieve Linux-desktop, niet een losse SSH-sessie.' >&2
  exit 1
fi
systemctl --user stop chatgpt-project-bot.service
"$ROOT/run.sh" --login
# Import only this explicitly selected desktop. Never guess another user's display.
systemctl --user unset-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS
for var in DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS; do
  if [[ -v "$var" ]]; then systemctl --user import-environment "$var"; fi
done
systemctl --user start chatgpt-project-bot.service
echo 'Desktop-watchdog gestart. Laat browser en desktop actief.'
