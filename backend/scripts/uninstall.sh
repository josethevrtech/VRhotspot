#!/usr/bin/env bash
set -euo pipefail

# VR Hotspot Backend Uninstaller

log() { echo "[backend-uninstall] $*"; }
die() { echo "[backend-uninstall] ERROR: $*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
    die "This script must be run as root."
fi

log "Stopping and disabling services..."
systemctl disable --now vr-hotspotd.service &>/dev/null || true
systemctl disable --now vr-hotspot-autostart.service &>/dev/null || true

log "Removing systemd unit files..."
rm -f /etc/systemd/system/vr-hotspotd.service
rm -f /etc/systemd/system/vr-hotspot-autostart.service
rm -rf /etc/systemd/system/vr-hotspotd.service.d
systemctl daemon-reload

log "Removing application and configuration files..."
rm -rf /var/lib/vr-hotspot
rm -rf /etc/vr-hotspot

log "Uninstallation complete."