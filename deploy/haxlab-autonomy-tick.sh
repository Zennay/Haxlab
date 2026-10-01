#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HAXLAB_APP_DIR:-/opt/haxlab}"
STATE_DIR="${HAXLAB_STATE_DIR:-/var/lib/haxlab/state}"
DERIVED_DIR="${HAXLAB_DERIVED_DIR:-/var/lib/haxlab/derived}"
MODELS_DIR="${HAXLAB_MODELS_DIR:-/var/lib/haxlab/models}"
STATUS_FILE="${STATE_DIR}/autonomy-status.json"
LOCK_FILE="${STATE_DIR}/autonomy.lock"

mkdir -p "${STATE_DIR}"
exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

write_status() {
  local state="$1" action="$2" detail="$3"
  STATE="$state" ACTION="$action" DETAIL="$detail" STATUS_FILE="$STATUS_FILE"     "${APP_DIR}/.venv/bin/python" - <<'PY'
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

STATUS_JSON="$("${APP_DIR}/.venv/bin/haxlab-status")"
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

if (( PROCESSING_PENDING > 0 || ANALYSIS_PENDING > 0 )); then
  write_status "RUNNING" "await_pipeline" "processing_pending=${PROCESSING_PENDING}; analysis_pending=${ANALYSIS_PENDING}; processing_failed=${PROCESSING_FAILED}; analysis_failed=${ANALYSIS_FAILED}"
  exit 0
fi

if (( ANALYSIS_OK == 0 )); then
  if (( PROCESSING_FAILED > 0 || ANALYSIS_FAILED > 0 )); then
    write_status "FAILED_RETRYABLE" "pipeline_failures" "No usable analyzed replay remains; processing_failed=${PROCESSING_FAILED}; analysis_failed=${ANALYSIS_FAILED}"
  else
    write_status "BLOCKED" "await_data" "No analyzed replay evidence is available yet."
  fi
  exit 0
fi

# Failed/corrupt source replays must not prevent safe deterministic work from the
# successfully analyzed corpus. They remain visible in status and are never
# silently promoted to usable evidence.
FAILURE_NOTE="processing_failed=${PROCESSING_FAILED}; analysis_failed=${ANALYSIS_FAILED}"

LEADERBOARD="${DERIVED_DIR}/leaderboards/${ANALYSIS_VERSION}.json"
MANIFEST="${DERIVED_DIR}/training/human-imitation-${ANALYSIS_VERSION}.json"

if [[ ! -s "${LEADERBOARD}" ]]; then
  write_status "RUNNING" "refresh_skill" "Building the current skill leaderboard; ${FAILURE_NOTE}"
  "${APP_DIR}/.venv/bin/haxlab-skill"     --top 30     --min-matches 20     --min-minutes 60     --output "${LEADERBOARD}"
fi

if [[ ! -s "${MANIFEST}" ]]; then
  write_status "RUNNING" "build_dataset_manifest" "Building the versioned human-imitation manifest; ${FAILURE_NOTE}"
  "${APP_DIR}/.venv/bin/haxlab-training-manifest"     --analysis-root "${DERIVED_DIR}/${ANALYSIS_VERSION}"     --leaderboard "${LEADERBOARD}"     --raw-root /var/lib/haxlab/raw/replays     --output "${MANIFEST}"
fi

# Do one real, reproducible autonomous compute step before the closed-loop gate:
# train a baseline challenger from the frozen human-imitation manifest. This is
# deliberately NOT a promotion action. The output becomes evidence for the later
# Arena v2 evaluator and cannot replace the champion by itself.
SHARD_ROOT="${DERIVED_DIR}/training/shards-autonomy-baseline/${ANALYSIS_VERSION}"
MODEL_DIR="${MODELS_DIR}/challengers/autonomy-bc-baseline-v1"
METRICS="${MODEL_DIR}/metrics.json"

if [[ ! -s "${METRICS}" ]]; then
  write_status "RUNNING" "train_baseline_challenger" "Building shards and training reproducible BC baseline; ${FAILURE_NOTE}"
  mkdir -p "${SHARD_ROOT}" "${MODEL_DIR}"

  "${APP_DIR}/.venv/bin/haxlab-build-shards"     --manifest "${MANIFEST}"     --split train     --limit 500     --workers 4     --sample-every-ticks 6     --output-root "${SHARD_ROOT}"

  "${APP_DIR}/.venv/bin/haxlab-build-shards"     --manifest "${MANIFEST}"     --split holdout     --limit 100     --workers 4     --sample-every-ticks 6     --output-root "${SHARD_ROOT}"

  "${APP_DIR}/.venv/bin/haxlab-train-bc"     --train-index "${SHARD_ROOT}/train/_index.json"     --holdout-index "${SHARD_ROOT}/holdout/_index.json"     --output-dir "${MODEL_DIR}"     --hidden-dim 64     --epochs 4     --batch-size 8192     --learning-rate 0.001     --l2 0.00001     --seed 1337

  if [[ ! -s "${METRICS}" ]]; then
    write_status "FAILED_RETRYABLE" "baseline_missing_metrics" "Training returned without metrics artifact: ${METRICS}"
    exit 0
  fi
fi

# The generation executor owns the rest of the loop. It creates an immutable
# preregistration, trains one bounded candidate, evaluates the frozen holdout,
# records promotion/rejection evidence, mines failures, and always rolls over
# to the next generation. No ChatGPT or external AI call is required.
write_status "RUNNING" "generation_executor" "Starting autonomous generation rollover; ${FAILURE_NOTE}"
"${APP_DIR}/.venv/bin/haxlab-generation-loop" \
  --app-dir "${APP_DIR}" \
  --state-dir "${STATE_DIR}" \
  --models-dir "${MODELS_DIR}" \
  --derived-dir "${DERIVED_DIR}" \
  --analysis-version "${ANALYSIS_VERSION}" \
  --manifest "${MANIFEST}" \
  --shard-root "${SHARD_ROOT}" \
  --max-generations-per-tick 1
