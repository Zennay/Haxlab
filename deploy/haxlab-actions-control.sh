#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
STATE_DB="${HAXLAB_STATE_DB:-/var/lib/haxlab/state/haxlab.sqlite3}"
CURRENT_ANALYZER_VERSION="$("${APP_DIR}/.venv/bin/python" -c 'from haxlab.runtime.state import CURRENT_ANALYZER_VERSION; print(CURRENT_ANALYZER_VERSION)')"

usage() {
  echo "Usage: haxlab-actions-control {status|deploy|restart-analyzer|retry-failed-analysis|feature-smoke|player-stats|skill-leaderboard|training-manifest|start-training-shards|stop-training-shards|training-shards-status|analyzer-logs|failed-analysis}" >&2
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
      FROM replay_analysis_versions
      WHERE analyzer_version='${CURRENT_ANALYZER_VERSION}' AND status='failed'
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
      UPDATE replay_analysis_versions
      SET status='retry', updated_at=CURRENT_TIMESTAMP
      WHERE analyzer_version='${CURRENT_ANALYZER_VERSION}' AND status='failed';
      SELECT changes();
    "
    systemctl start haxlab-analyzer.service
    systemctl is-active haxlab-analyzer.service
    haxlab-status
    ;;

  feature-smoke)
    replay_path=""
    for _ in {1..30}; do
      replay_path="$(sqlite3 "${STATE_DB}" "
        SELECT r.archive_path
        FROM raw_replays AS r
        JOIN replay_processing AS p
          ON p.sha256 = r.sha256 AND p.status = 'ok'
        JOIN replay_analysis_versions AS a
          ON a.sha256 = r.sha256
         AND a.analyzer_version = '${CURRENT_ANALYZER_VERSION}'
         AND a.status = 'ok'
        WHERE COALESCE(a.player_count, 0) >= 6
          AND COALESCE(p.total_frames, 0) >= 10000
        ORDER BY ABS(r.size_bytes - 40000), r.first_archived_at
        LIMIT 1;
      ")"
      if [[ -n "${replay_path}" ]]; then
        break
      fi
      echo "Waiting for first ${CURRENT_ANALYZER_VERSION} replay..."
      sleep 2
    done
    if [[ -z "${replay_path}" ]]; then
      echo "No replay available for feature smoke test after 60 seconds." >&2
      exit 1
    fi

    tmp_json="$(mktemp)"
    trap 'rm -f "${tmp_json}"' EXIT
    node "${APP_DIR}/tools/decode_replay.js" "${replay_path}" 6 >"${tmp_json}"

    "${APP_DIR}/.venv/bin/python" - "${tmp_json}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)

summary = payload.get("featureSummary") or {}
sparse = payload.get("sparseEvents") or {}
players = payload.get("players") or []

print("schemaVersion:", payload.get("schemaVersion"))
print("featureVersion:", payload.get("featureVersion"))
print("frames:", payload.get("totalFrames"))
print("featureSummary:", json.dumps(summary, sort_keys=True))
print(
    "sparseCounts:",
    json.dumps(
        {
            "touches": len(sparse.get("touches") or []),
            "kicks": len(sparse.get("kicks") or []),
            "goals": len(sparse.get("goals") or []),
        },
        sort_keys=True,
    ),
)

top = sorted(players, key=lambda p: int(p.get("touches") or 0), reverse=True)[:8]
print("top touch players:")
for player in top:
    print(
        " -",
        player.get("name"),
        "touches=", player.get("touches"),
        "teamTransfers=", player.get("teamTouchTransfersOut"),
        "turnovers=", player.get("turnovers"),
        "pressureRate=", player.get("underPressureTouchRate"),
    )

if int(payload.get("schemaVersion") or 0) < 4:
    raise SystemExit("feature smoke failed: expected schemaVersion >= 4")
if int(summary.get("touches") or 0) <= 0:
    raise SystemExit("feature smoke failed: no logical touches detected")
if int(summary.get("ballCollisionEvents") or 0) < int(summary.get("touches") or 0):
    raise SystemExit("feature smoke failed: collision count below logical touches")

touch_rows = sparse.get("touches") or []
if len(touch_rows) != int(summary.get("touches") or 0):
    raise SystemExit("feature smoke failed: sparse touch count mismatch")
if not any(int(row[7] or 0) in (2, 3) for row in touch_rows if len(row) > 7):
    raise SystemExit("feature smoke failed: no teammate/opponent touch transitions")
PY
    ;;

  player-stats)
    if command -v haxlab-players >/dev/null 2>&1; then
      haxlab-players --top 30 --min-matches 20 --min-minutes 30
    else
      "${APP_DIR}/.venv/bin/python" -m haxlab.runtime.player_stats \
        --top 30 --min-matches 20 --min-minutes 30
    fi
    ;;

  skill-leaderboard)
    leaderboard_dir="/var/lib/haxlab/derived/leaderboards"
    leaderboard_json="${leaderboard_dir}/${CURRENT_ANALYZER_VERSION}.json"
    mkdir -p "${leaderboard_dir}"

    if command -v haxlab-skill >/dev/null 2>&1; then
      haxlab-skill \
        --top 30 \
        --min-matches 20 \
        --min-minutes 60 \
        --output "${leaderboard_json}"
    else
      "${APP_DIR}/.venv/bin/python" -m haxlab.skill.leaderboard \
        --top 30 \
        --min-matches 20 \
        --min-minutes 60 \
        --output "${leaderboard_json}"
    fi
    echo
    echo "Leaderboard snapshot: ${leaderboard_json}"
    ;;

  training-manifest)
    leaderboard="/var/lib/haxlab/derived/leaderboards/${CURRENT_ANALYZER_VERSION}.json"
    output="/var/lib/haxlab/derived/training/human-imitation-${CURRENT_ANALYZER_VERSION}.json"
    if [[ ! -f "${leaderboard}" ]]; then
      echo "Leaderboard is not finalized yet: ${leaderboard}" >&2
      exit 1
    fi
    haxlab-training-manifest \
      --analysis-root "/var/lib/haxlab/derived/${CURRENT_ANALYZER_VERSION}" \
      --leaderboard "${leaderboard}" \
      --raw-root /var/lib/haxlab/raw/replays \
      --output "${output}"
    ;;

  start-training-shards)
    systemctl start haxlab-training-shards.service
    systemctl status haxlab-training-shards.service --no-pager -l || true
    ;;

  stop-training-shards)
    systemctl stop haxlab-training-shards.service
    systemctl status haxlab-training-shards.service --no-pager -l || true
    ;;

  training-shards-status)
    echo "=== service ==="
    systemctl status haxlab-training-shards.service --no-pager -l || true
    echo
    echo "=== train progress ==="
    cat "/var/lib/haxlab/derived/training/shards/${CURRENT_ANALYZER_VERSION}/train/_progress.json" 2>/dev/null || echo "no train progress yet"
    echo
    echo "=== holdout progress ==="
    cat "/var/lib/haxlab/derived/training/shards/${CURRENT_ANALYZER_VERSION}/holdout/_progress.json" 2>/dev/null || echo "no holdout progress yet"
    echo
    echo "=== completion ==="
    cat "/var/lib/haxlab/derived/training/shards/${CURRENT_ANALYZER_VERSION}/_complete.json" 2>/dev/null || echo "not complete yet"
    ;;

  analyzer-logs)
    journalctl -u haxlab-analyzer.service -n 120 --no-pager
    ;;

  failed-analysis)
    sqlite3 "${STATE_DB}" "
      SELECT COALESCE(error, '<no error>') AS error, COUNT(*) AS count
      FROM replay_analysis_versions
      WHERE analyzer_version='${CURRENT_ANALYZER_VERSION}' AND status='failed'
      GROUP BY error
      ORDER BY count DESC
      LIMIT 30;
    "
    ;;

  *)
    usage
    ;;
esac
