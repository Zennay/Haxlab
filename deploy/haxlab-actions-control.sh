#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
STATE_DB="${HAXLAB_STATE_DB:-/var/lib/haxlab/state/haxlab.sqlite3}"

usage() {
  echo "Usage: haxlab-actions-control {status|deploy|restart-analyzer|retry-failed-analysis|player-stats|analyzer-logs|failed-analysis}" >&2
  exit 2
}

action="${1:-}"
case "${action}" in
  status)
    echo "=== services ==="
    systemctl is-active haxlab-ingest.service || true
    systemctl is-active haxlab-worker.service || true
    systemctl is-active haxlab-analyzer.service || true
    echo
    echo "=== haxlab-status ==="
    haxlab-status
    ;;

  deploy)
    echo "=== deploying origin/main ==="
    bash "${APP_DIR}/deploy/update-vps.sh"
    echo
    echo "=== post-deploy status ==="
    haxlab-status

    echo
    echo "=== preliminary player leaderboard ==="
    if command -v haxlab-players >/dev/null 2>&1; then
      haxlab-players --top 20 --min-matches 20 --min-minutes 30 || true
    else
      "${APP_DIR}/.venv/bin/python" -m haxlab.runtime.player_stats \
        --top 20 --min-matches 20 --min-minutes 30 || true
    fi

    echo
    echo "=== top analysis failure reasons ==="
    sqlite3 "${STATE_DB}" "
      SELECT COALESCE(error, '<no error>') AS error, COUNT(*) AS count
      FROM replay_analysis
      WHERE analyzer_version='state-pass-v3' AND status='failed'
      GROUP BY error
      ORDER BY count DESC
      LIMIT 15;
    " || true
    ;;

  restart-analyzer)
    systemctl restart haxlab-analyzer.service
    systemctl is-active haxlab-analyzer.service
    haxlab-status
    ;;

  retry-failed-analysis)
    systemctl stop haxlab-analyzer.service
    sqlite3 "${STATE_DB}" "
      UPDATE replay_analysis
      SET status='retry', updated_at=CURRENT_TIMESTAMP
      WHERE analyzer_version='state-pass-v3' AND status='failed';
      SELECT changes();
    "
    systemctl start haxlab-analyzer.service
    systemctl is-active haxlab-analyzer.service
    haxlab-status
    ;;

  player-stats)
    if command -v haxlab-players >/dev/null 2>&1; then
      haxlab-players --top 30 --min-matches 20 --min-minutes 30
    else
      "${APP_DIR}/.venv/bin/python" -m haxlab.runtime.player_stats \
        --top 30 --min-matches 20 --min-minutes 30
    fi
    ;;

  analyzer-logs)
    journalctl -u haxlab-analyzer.service -n 120 --no-pager
    ;;

  failed-analysis)
    sqlite3 "${STATE_DB}" "
      SELECT COALESCE(error, '<no error>') AS error, COUNT(*) AS count
      FROM replay_analysis
      WHERE analyzer_version='state-pass-v3' AND status='failed'
      GROUP BY error
      ORDER BY count DESC
      LIMIT 30;
    "
    ;;

  *)
    usage
    ;;
esac
