#!/usr/bin/env bash
set -euo pipefail

# Autostart helper: waits for daemon and triggers repair+start if not running.
# Reads /etc/vr-hotspot/env if present.

ENV_FILE="/etc/vr-hotspot/env"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

PORT="${VR_HOTSPOTD_PORT:-8732}"
BASE="http://127.0.0.1:${PORT}"
CID="autostart-$(date +%s)"

TOKEN="${VR_HOTSPOTD_API_TOKEN:-}"

hdrs=()
if [[ -n "${TOKEN}" ]]; then
  hdrs+=(-H "X-Api-Token: ${TOKEN}")
fi
hdrs+=(-H "X-Correlation-Id: ${CID}")

log() { echo "$*"; }

log "starting; waiting for API ${BASE}"

for _ in $(seq 1 80); do
  if curl -fsS "${BASE}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

st="$(curl -fsS "${hdrs[@]}" "${BASE}/v1/status" 2>/dev/null || true)"
running="$(python3 - <<'PY' "${st}"
import json
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    payload = json.loads(raw) if raw else {}
    data = payload.get("data") or {}
    print("true" if bool(data.get("running")) else "false")
except Exception:
    print("false")
PY
)"

log "daemon reachable; running=${running}"

if [[ "${running}" == "true" ]]; then
  log "already running; exiting"
  exit 0
fi

log "calling /v1/repair"
curl -fsS -X POST "${hdrs[@]}" "${BASE}/v1/repair" >/dev/null 2>&1 || true

log "calling /v1/start"
curl -fsS -X POST "${hdrs[@]}" "${BASE}/v1/start" >/dev/null 2>&1 || true

log "start request sent"
exit 0
