#!/usr/bin/env bash
set -euo pipefail

# VR Hotspot Backend Uninstaller

log() { echo "[backend-uninstall] $*"; }
die() { echo "[backend-uninstall] ERROR: $*" >&2; exit 1; }

DAEMON_UNIT="vr-hotspotd.service"
AUTOSTART_UNIT="vr-hotspot-autostart.service"
# Backward-compat cleanup only.
LEGACY_SYSTEMD_UNITS=("vr-hotspotd-autostart.service")
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID}" -ne 0 ]]; then
    die "This script must be run as root."
fi

log "Stopping and disabling services..."
for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
    systemctl disable --now "$unit" &>/dev/null || true
done

log "Removing systemd unit files..."
for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
    rm -f "$SYSTEMD_DIR/$unit"
done
rm -rf "$SYSTEMD_DIR/$DAEMON_UNIT.d"
systemctl daemon-reload

log "Removing application and configuration files..."
rm -rf /var/lib/vr-hotspot
rm -rf /etc/vr-hotspot

log "Uninstallation complete."
