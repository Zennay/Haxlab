#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"

echo "== systemd =="
systemctl --no-pager --full status haxlab-ingest.service haxlab-worker.service || true

echo
echo "== HaxLab =="
"${APP_DIR}/.venv/bin/python" -m haxlab.runtime.status

echo
echo "== disk =="
du -sh /var/lib/haxlab/incoming /var/lib/haxlab/raw /var/lib/haxlab/state 2>/dev/null || true
df -h /var/lib/haxlab
