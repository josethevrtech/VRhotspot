#!/usr/bin/env bash
set -euo pipefail

VR_HOTSPOTD_HOST="${VR_HOTSPOTD_HOST:-127.0.0.1}"
VR_HOTSPOTD_PORT="${VR_HOTSPOTD_PORT:-8732}"

url="http://${VR_HOTSPOTD_HOST}:${VR_HOTSPOTD_PORT}/healthz"

for _ in $(seq 1 80); do
  if curl -fsS --max-time 0.3 "$url" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.1
done

echo "vr-hotspotd did not become healthy in time" >&2
exit 1
