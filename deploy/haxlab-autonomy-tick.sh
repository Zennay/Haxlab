#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
STATE_DIR="${HAXLAB_STATE_DIR:-/var/lib/haxlab/state}"
DERIVED_DIR="${HAXLAB_DERIVED_DIR:-/var/lib/haxlab/derived}"
STATUS_FILE="${STATE_DIR}/autonomy-status.json"
LOCK_FILE="${STATE_DIR}/autonomy.lock"

mkdir -p "${STATE_DIR}"
exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

write_status() {
  local state="$1" action="$2" detail="$3"
  STATE="$state" ACTION="$action" DETAIL="$detail" STATUS_FILE="$STATUS_FILE" \
    "${APP_DIR}/.venv/bin/python" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["STATUS_FILE"])
payload = {
    "schema": "haxlab-autonomy-status-v1",
    "state": os.environ["STATE"],
    "action": os.environ["ACTION"],
    "detail": os.environ["DETAIL"],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

STATUS_JSON="$(${APP_DIR}/.venv/bin/haxlab-status)"
read -r PROCESSING_PENDING ANALYSIS_PENDING PROCESSING_FAILED ANALYSIS_FAILED ANALYSIS_OK ANALYSIS_VERSION < <(
  STATUS_JSON="$STATUS_JSON" "${APP_DIR}/.venv/bin/python" - <<'PY'
import json
import os
p=json.loads(os.environ["STATUS_JSON"])
print(
    int(p.get("processing_pending", 0)),
    int(p.get("analysis_pending", 0)),
    int(p.get("processing_failed", 0)),
    int(p.get("analysis_failed", 0)),
    int(p.get("analysis_ok", 0)),
    str(p.get("analysis_version") or "unknown"),
)
PY
)

if (( PROCESSING_FAILED > 0 || ANALYSIS_FAILED > 0 )); then
  write_status "FAILED_RETRYABLE" "pipeline_failures" "processing_failed=${PROCESSING_FAILED}; analysis_failed=${ANALYSIS_FAILED}"
  exit 0
fi

if (( PROCESSING_PENDING > 0 || ANALYSIS_PENDING > 0 )); then
  write_status "RUNNING" "await_pipeline" "processing_pending=${PROCESSING_PENDING}; analysis_pending=${ANALYSIS_PENDING}"
  exit 0
fi

if (( ANALYSIS_OK == 0 )); then
  write_status "BLOCKED" "await_data" "No analyzed replay evidence is available yet."
  exit 0
fi

LEADERBOARD="${DERIVED_DIR}/leaderboards/${ANALYSIS_VERSION}.json"
MANIFEST="${DERIVED_DIR}/training/human-imitation-${ANALYSIS_VERSION}.json"

if [[ ! -s "${LEADERBOARD}" ]]; then
  write_status "RUNNING" "refresh_skill" "Building the current skill leaderboard."
  "${APP_DIR}/.venv/bin/haxlab-skill" \
    --top 30 \
    --min-matches 20 \
    --min-minutes 60 \
    --output "${LEADERBOARD}"
fi

if [[ ! -s "${MANIFEST}" ]]; then
  write_status "RUNNING" "build_dataset_manifest" "Building the versioned human-imitation manifest."
  "${APP_DIR}/.venv/bin/haxlab-training-manifest" \
    --analysis-root "${DERIVED_DIR}/${ANALYSIS_VERSION}" \
    --leaderboard "${LEADERBOARD}" \
    --raw-root /var/lib/haxlab/raw/replays \
    --output "${MANIFEST}"
fi

# The current main branch deliberately does not auto-train/promote from the old
# controller contract. Closed-loop Arena v2 + guard-independence must be the
# audited executor boundary before autonomous challenger mutation is enabled.
if [[ ! -f "${APP_DIR}/tools/elite_closed_loop_arena_v2.js" ]]; then
  write_status "NEEDS_AI" "closed_loop_executor_not_on_main" "Deterministic data preparation is complete; merge/audit the Arena v2 executor before autonomous training/promotion."
  exit 0
fi

write_status "NEEDS_AI" "research_gate_ready" "Arena v2 code is present, but autonomous challenger mutation remains fail-closed until its executor is explicitly audited."
