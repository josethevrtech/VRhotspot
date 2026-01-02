#!/usr/bin/env bash
set -euo pipefail

# VR Hotspot uninstaller
# Default: remove services + app code; keep /etc/vr-hotspot env + /var/lib/vr-hotspot root for user data.
# Use --purge to remove everything including config/state/env.

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[uninstall] $*"; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run as root (sudo)."
  fi
}

PURGE="no"

usage() {
  cat <<'USAGE'
Usage:
  sudo bash backend/scripts/uninstall.sh [--purge]

Options:
  --purge   Remove /var/lib/vr-hotspot and /etc/vr-hotspot entirely (including env/config/state)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE="yes"; shift 1;;
    -h|--help) usage; exit 0;;
    *) die "Unknown arg: $1 (use --help)";;
  esac
done

require_root

APP_ROOT="/var/lib/vr-hotspot"
APP_DIR="${APP_ROOT}/app"
BIN_DIR="${APP_ROOT}/bin"
ETC_DIR="/etc/vr-hotspot"

UNIT_DAEMON="/etc/systemd/system/vr-hotspotd.service"
UNIT_AUTOSTART="/etc/systemd/system/vr-hotspot-autostart.service"
UNIT_DAEMON_DROPIN="/etc/systemd/system/vr-hotspotd.service.d"

log "Stopping services (if present)"
systemctl disable --now vr-hotspot-autostart.service >/dev/null 2>&1 || true
systemctl disable --now vr-hotspotd.service >/dev/null 2>&1 || true

log "Removing unit files"
rm -f "${UNIT_DAEMON}" "${UNIT_AUTOSTART}" || true
rm -rf "${UNIT_DAEMON_DROPIN}" || true

log "Reloading systemd"
systemctl daemon-reload || true

log "Removing app code under ${APP_DIR} and autostart script under ${BIN_DIR}"
rm -rf "${APP_DIR}" || true
rm -f "${BIN_DIR}/vr-hotspot-autostart.sh" || true
rmdir "${BIN_DIR}" 2>/dev/null || true

if [[ "${PURGE}" == "yes" ]]; then
  log "Purging ${APP_ROOT} and ${ETC_DIR}"
  rm -rf "${APP_ROOT}" "${ETC_DIR}" || true
else
  log "Keeping ${APP_ROOT} root (state/config) and ${ETC_DIR} env"
  echo "Tip: run with --purge to remove ${APP_ROOT} and ${ETC_DIR} completely."
fi

log "Done."
