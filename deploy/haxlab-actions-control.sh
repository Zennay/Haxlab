#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
STATE_DB="${HAXLAB_STATE_DB:-/var/lib/haxlab/state/haxlab.sqlite3}"
CURRENT_ANALYZER_VERSION="$("${APP_DIR}/.venv/bin/python" -c 'from haxlab.runtime.state import CURRENT_ANALYZER_VERSION; print(CURRENT_ANALYZER_VERSION)')"

usage() {
  echo "Usage: haxlab-actions-control {status|deploy|audit-archive|audit-analysis|champion-info|champion-runtime|live-play-probe|live-play-start ROOM_ID|live-play-stop|live-play-status|live-host-prereq|live-host-keygen|live-host-install-token CIPHERTEXT|live-host-start|live-host-status|restart-analyzer|retry-failed-analysis|feature-smoke|player-stats|skill-leaderboard|training-manifest|analyzer-logs|failed-analysis}" >&2
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

  audit-archive)
    echo "=== raw archive integrity audit ==="
    "${APP_DIR}/.venv/bin/haxlab-audit-archive" --state-db "${STATE_DB}" --max-issues 100
    ;;

  audit-analysis)
    echo "=== analysis artifact integrity audit ==="
    "${APP_DIR}/.venv/bin/haxlab-audit-analysis" \
      --state-db "${STATE_DB}" \
      --derived-root /var/lib/haxlab/derived \
      --max-issues 100
    ;;

  champion-info)
    root="/var/lib/haxlab/derived/champions/elite-player"
    echo "=== champion root ==="
    if [[ ! -d "${root}" ]]; then
      echo "missing: ${root}" >&2
      exit 1
    fi
    ls -la "${root}" || true
    echo
    "${APP_DIR}/.venv/bin/python" - "${root}" <<'PY'
import json
import os
import sys
from pathlib import Path

import numpy as np

root = Path(sys.argv[1])
metrics_files = sorted(
    root.glob("versions/*/metrics.json"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
if not metrics_files:
    raise SystemExit("no champion metrics found")

rows = []
for metrics_path in metrics_files[:8]:
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as exc:
        rows.append({"path": str(metrics_path), "error": str(exc)})
        continue
    version_dir = metrics_path.parent
    model_candidates = sorted(version_dir.glob("*.npz"))
    model_info = []
    for model_path in model_candidates:
        try:
            with np.load(model_path, allow_pickle=False) as data:
                model_info.append({
                    "path": str(model_path),
                    "keys": sorted(data.files),
                    "shapes": {k: list(data[k].shape) for k in data.files},
                })
        except Exception as exc:
            model_info.append({"path": str(model_path), "error": str(exc)})
    rows.append({
        "version": version_dir.name,
        "metrics_path": str(metrics_path),
        "metrics": metrics,
        "models": model_info,
    })

runtime_documents = []
for row in rows[:3]:
    if not isinstance(row, dict):
        continue
    metrics = row.get("metrics") or {}
    runtime_path = metrics.get("runtime_model_path")
    if not runtime_path:
        continue
    path = Path(str(runtime_path))
    if not path.is_file():
        runtime_documents.append({"path": str(path), "error": "missing"})
        continue
    try:
        runtime_documents.append({
            "path": str(path),
            "payload": json.loads(path.read_text(encoding="utf-8")),
        })
    except Exception as exc:
        runtime_documents.append({"path": str(path), "error": str(exc)})

print(json.dumps({
    "root": str(root),
    "versions": rows,
    "runtime_documents": runtime_documents,
}, indent=2, sort_keys=True))
PY
    ;;

  champion-runtime)
    root="/var/lib/haxlab/derived/champions/elite-player"
    "${APP_DIR}/.venv/bin/python" - "${root}" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
metrics_files = sorted(
    root.glob("versions/*/metrics.json"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
if not metrics_files:
    raise SystemExit("no champion metrics found")
metrics_path = metrics_files[0]
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
runtime_path = Path(str(metrics.get("runtime_model_path") or ""))
runtime = None
if runtime_path.is_file():
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
print(json.dumps({
    "version": metrics_path.parent.name,
    "metrics_path": str(metrics_path),
    "runtime_model_path": str(runtime_path),
    "architecture": metrics.get("architecture"),
    "base_input_columns": metrics.get("base_input_columns"),
    "runtime": metrics.get("runtime"),
    "training": metrics.get("training"),
    "runtime_model": runtime,
}, indent=2, sort_keys=True))
PY
    ;;

  live-play-probe)
    "${APP_DIR}/.venv/bin/python" -m haxlab.live.inference --probe
    node --check "${APP_DIR}/tools/live_haxball_bot.js"
    ;;

  live-play-start)
    room_id="${2:-}"
    if [[ ! "${room_id}" =~ ^[A-Za-z0-9_-]{4,80}$ ]]; then
      echo "invalid room id" >&2
      exit 2
    fi
    env_file="/var/lib/haxlab/state/live-play.env"
    tmp_file="$(mktemp)"
    {
      printf 'HAXLAB_ROOM_ID=%s\n' "${room_id}"
      printf 'HAXLAB_PLAYER_NAME=%s\n' "HaxLab AI"
      printf 'HAXLAB_PLAYER_AVATAR=%s\n' "AI"
      printf 'HAXLAB_LIVE_ROLE=%s\n' "st"
      printf 'HAXLAB_INFER_EVERY_TICKS=%s\n' "2"
    } >"${tmp_file}"
    install -o haxlab -g haxlab -m 0600 "${tmp_file}" "${env_file}"
    rm -f "${tmp_file}"
    systemctl restart haxlab-live-bot.service
    sleep 2
    systemctl --no-pager --full status haxlab-live-bot.service || true
    journalctl -u haxlab-live-bot.service -n 80 --no-pager
    ;;

  live-play-stop)
    systemctl stop haxlab-live-bot.service 2>/dev/null || true
    echo "stopped"
    ;;

  live-play-status)
    systemctl --no-pager --full status haxlab-live-bot.service || true
    echo
    if [[ -s /var/lib/haxlab/state/live-room-link ]]; then
      echo "=== room link ==="
      cat /var/lib/haxlab/state/live-room-link
      echo
    fi
    journalctl -u haxlab-live-bot.service -n 120 --no-pager || true
    ;;

  live-host-prereq)
    token_file="/var/lib/haxlab/state/haxball-headless-token"
    if [[ -s "${token_file}" ]]; then
      token_len="$(tr -d '\r\n' <"${token_file}" | wc -c | tr -d ' ')"
      echo "headless_token=present"
      echo "headless_token_length=${token_len}"
    else
      echo "headless_token=missing"
      echo "token_url=https://www.haxball.com/headlesstoken"
      exit 3
    fi
    ;;

  live-host-keygen)
    private_key="/var/lib/haxlab/state/live-host-token-key.pem"
    public_key="/var/lib/haxlab/state/live-host-token-key.pub.pem"
    if [[ ! -s "${private_key}" ]]; then
      openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${private_key}" >/dev/null 2>&1
      chown root:root "${private_key}"
      chmod 0600 "${private_key}"
    fi
    openssl pkey -in "${private_key}" -pubout -out "${public_key}" >/dev/null 2>&1
    chmod 0644 "${public_key}"
    echo "=== live host token public key ==="
    cat "${public_key}"
    ;;

  live-host-install-token)
    ciphertext="${2:-}"
    private_key="/var/lib/haxlab/state/live-host-token-key.pem"
    token_file="/var/lib/haxlab/state/haxball-headless-token"
    if [[ ! -s "${private_key}" ]]; then
      echo "missing host-token private key; run live-host-keygen first" >&2
      exit 3
    fi
    if [[ ! "${ciphertext}" =~ ^[A-Za-z0-9+/=]{100,1000}$ ]]; then
      echo "invalid encrypted token payload" >&2
      exit 2
    fi
    encrypted_tmp="$(mktemp)"
    token_tmp="$(mktemp)"
    trap 'rm -f "${encrypted_tmp}" "${token_tmp}"' EXIT
    printf '%s' "${ciphertext}" | base64 -d >"${encrypted_tmp}"
    openssl pkeyutl -decrypt       -inkey "${private_key}"       -in "${encrypted_tmp}"       -out "${token_tmp}"       -pkeyopt rsa_padding_mode:oaep       -pkeyopt rsa_oaep_md:sha256
    token="$(tr -d '\r\n' <"${token_tmp}")"
    if [[ ${#token} -lt 20 || ${#token} -gt 512 ]]; then
      echo "decrypted token has invalid length" >&2
      exit 3
    fi
    printf '%s\n' "${token}" >"${token_tmp}"
    install -o root -g root -m 0600 "${token_tmp}" "${token_file}"
    echo "headless_token=installed_securely"
    trap - EXIT
    rm -f "${encrypted_tmp}" "${token_tmp}"
    "$0" live-host-start
    ;;

  live-host-start)
    token_file="/var/lib/haxlab/state/haxball-headless-token"
    token=""
    if [[ -s "${token_file}" ]]; then
      token="$(tr -d '\r\n' <"${token_file}")"
      if [[ ${#token} -lt 20 || ${#token} -gt 512 ]]; then
        echo "headless_token=invalid_length" >&2
        exit 3
      fi
      echo "headless_token=present"
    else
      echo "headless_token=missing_trying_without_token"
    fi
    env_file="/var/lib/haxlab/state/live-play.env"
    tmp_file="$(mktemp)"
    {
      printf 'HAXLAB_LIVE_MODE=%s\n' "host"
      if [[ -n "${token}" ]]; then
        printf 'HAXLAB_HEADLESS_TOKEN=%s\n' "${token}"
      fi
      printf 'HAXLAB_HOST_ROOM_NAME=%s\n' "HaxLab AI Challenge"
      printf 'HAXLAB_HOST_MAX_PLAYERS=%s\n' "4"
      printf 'HAXLAB_HOST_PUBLIC=%s\n' "0"
      printf 'HAXLAB_ROOM_LINK_FILE=%s\n' "/var/lib/haxlab/state/live-room-link"
      printf 'HAXLAB_PLAYER_NAME=%s\n' "HaxLab AI"
      printf 'HAXLAB_PLAYER_AVATAR=%s\n' "AI"
      printf 'HAXLAB_LIVE_ROLE=%s\n' "st"
      printf 'HAXLAB_INFER_EVERY_TICKS=%s\n' "2"
    } >"${tmp_file}"
    install -o haxlab -g haxlab -m 0600 "${tmp_file}" "${env_file}"
    rm -f "${tmp_file}"
    rm -f /var/lib/haxlab/state/live-room-link
    systemctl restart haxlab-live-bot.service
    for _ in {1..15}; do
      if [[ -s /var/lib/haxlab/state/live-room-link ]]; then
        break
      fi
      if ! systemctl is-active --quiet haxlab-live-bot.service; then
        break
      fi
      sleep 1
    done
    systemctl --no-pager --full status haxlab-live-bot.service || true
    echo
    if [[ -s /var/lib/haxlab/state/live-room-link ]]; then
      echo "=== room link ==="
      cat /var/lib/haxlab/state/live-room-link
    else
      echo "room_link=pending_or_failed"
    fi
    echo
    journalctl -u haxlab-live-bot.service -n 120 --no-pager || true
    test -s /var/lib/haxlab/state/live-room-link
    ;;

  live-host-status)
    systemctl --no-pager --full status haxlab-live-bot.service || true
    echo
    if [[ -s /var/lib/haxlab/state/live-room-link ]]; then
      echo "=== room link ==="
      cat /var/lib/haxlab/state/live-room-link
    else
      echo "room_link=missing"
    fi
    echo
    journalctl -u haxlab-live-bot.service -n 120 --no-pager || true
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
