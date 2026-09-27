#!/usr/bin/env bash
set -Eeuo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

echo "=== identity ==="
id
command -v node
node --version
echo

echo "=== zSSH runtime pointers ==="
readlink -f /home/ubuntu/.local/share/zssh/current || true
ls -ld /home/ubuntu/.local/share/zssh /home/ubuntu/.local/share/zssh/current 2>/dev/null || true
ls -l /home/ubuntu/.config/systemd/user/zssh.service 2>/dev/null || true
stat -c '%a %U:%G %n' /home/ubuntu/.config/zssh/zssh.env 2>/dev/null || true
if [[ -f /home/ubuntu/.config/zssh/zssh.env ]]; then
  cut -d= -f1 /home/ubuntu/.config/zssh/zssh.env | sed '/^$/d' | sort
fi
echo

echo "=== zSSH user service ==="
systemctl --user status zssh.service --no-pager -l || true
echo
journalctl --user -u zssh.service -n 120 --no-pager || true
echo

echo "=== live zCloud managed drift hashes ==="
for f in enhancements.py resource-policy.json; do
  if [[ -f "/home/ubuntu/zennay-cloud/$f" ]]; then
    sha256sum "/home/ubuntu/zennay-cloud/$f"
  else
    echo "MISSING /home/ubuntu/zennay-cloud/$f"
  fi
done
echo

echo "=== zCloud prechange JSON ==="
/home/ubuntu/.local/bin/zcloud-prechange-guard   --root /home/ubuntu/zennay-cloud   --state /home/ubuntu/.local/state/zcloud/recovery   --json || true
