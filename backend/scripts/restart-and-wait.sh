#!/usr/bin/env sh
set -u

SERVICE="vr-hotspotd.service"
URL="http://127.0.0.1:8732/healthz"
TIMEOUT_S=10

echo "Restarting ${SERVICE}..."
if ! sudo systemctl restart "${SERVICE}"; then
  echo "Warning: systemctl restart failed; continuing to health check."
fi

start_ts=$(date +%s)
ok=0

while [ $(( $(date +%s) - start_ts )) -lt "${TIMEOUT_S}" ]; do
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -s -o /dev/null -w "%{http_code}" "${URL}" 2>/dev/null || true)
  elif command -v wget >/dev/null 2>&1; then
    code=$(wget -q -O /dev/null --server-response "${URL}" 2>&1 | awk '/^  HTTP/{print $2}' | tail -n 1)
  else
    code=""
  fi

  if [ "${code}" = "200" ]; then
    ok=1
    break
  fi
  sleep 0.25
done

if [ "${ok}" -eq 1 ]; then
  echo "Health check OK: ${URL}"
  exit 0
fi

echo "Health check failed after ${TIMEOUT_S}s: ${URL}"
echo "== systemctl status =="
sudo systemctl status --no-pager -l "${SERVICE}" || true
echo "== journal tail =="
sudo journalctl -u "${SERVICE}" -n 200 --no-pager || true
echo "== ss listener (port 8732) =="
if command -v rg >/dev/null 2>&1; then
  ss -lntp | rg ':8732' || true
else
  ss -lntp | grep ':8732' || true
fi

exit 1
