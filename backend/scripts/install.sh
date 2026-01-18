#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
VR Hotspot Backend Installer

Usage:
  sudo bash backend/scripts/install.sh [options]

This script is meant to be called by the main installer. It handles the
installation of the backend files and systemd services.

Options:
  --install-dir <path>      Install backend into this directory (default: /var/lib/vr-hotspot/app)
  --enable-autostart        Enable hotspot autostart on boot (config only)
  --disable-autostart       Disable hotspot autostart on boot (config only)
  -h, --help                 Show this help
USAGE
}

log() { echo "[backend-install] $*"; }
die() { echo "[backend-install] ERROR: $*" >&2; exit 1; }

DEFAULT_INSTALL_DIR="/var/lib/vr-hotspot/app"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
ENABLE_AUTOSTART="0"
FIX_AUTOSTART_CONFIG="0"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --enable-autostart) ENABLE_AUTOSTART="1"; FIX_AUTOSTART_CONFIG="1"; shift ;;
    --disable-autostart) ENABLE_AUTOSTART="0"; FIX_AUTOSTART_CONFIG="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# Locate repo root and backend dir based on script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_SRC="$REPO_ROOT/backend"

[[ -d "$BACKEND_SRC/vr_hotspotd" ]] || die "Expected $BACKEND_SRC/vr_hotspotd not found. Are you in the right repo?"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found (systemd required)"

# Create dirs
APP_ROOT="/var/lib/vr-hotspot"
VENV_DIR="$APP_ROOT/venv"
install -d -m 755 "$APP_ROOT"

log "Copying application files -> $INSTALL_DIR"
# Copy source code
mkdir -p "$INSTALL_DIR"
cp -r "$BACKEND_SRC/../." "$INSTALL_DIR/"
cp -a "$BACKEND_SRC/vendor" "$INSTALL_DIR/backend/"
if [ -d "$INSTALL_DIR/backend/vendor/bin/bazzite" ]; then
  log "INFO: bazzite_vendor_bundle_present=1"
else
  log "INFO: bazzite_vendor_bundle_present=0"
fi

# Ensure vendor binaries are executable if present
if [[ -d "$INSTALL_DIR/backend/vendor/bin" ]]; then
  chmod +x "$INSTALL_DIR/backend/vendor/bin/"* 2>/dev/null || true
fi

# Create Python virtual environment
log "Creating Python virtual environment at $VENV_DIR..."
python3 -m venv "$VENV_DIR"
log "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --no-cache-dir -U pip &>/dev/null
"$VENV_DIR/bin/pip" install --no-cache-dir "$INSTALL_DIR" &>/dev/null


# Install systemd units
SYSTEMD_DST="/etc/systemd/system"
UNIT_DAEMON="${SYSTEMD_DST}/vr-hotspotd.service"
log "Installing systemd units into $SYSTEMD_DST"

cat > "$UNIT_DAEMON" <<EOF
[Unit]
Description=VR Hotspot Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment="LD_LIBRARY_PATH=${INSTALL_DIR}/backend/vendor/lib"
Environment="VR_HOTSPOT_INSTALL_DIR=${INSTALL_DIR}"
EnvironmentFile=/etc/vr-hotspot/env
ExecStart=$VENV_DIR/bin/python -m vr_hotspotd.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

log "Reloading systemd"
systemctl daemon-reload

log "Enabling vr-hotspotd.service (daemon always enabled)"
systemctl enable vr-hotspotd.service

log "Starting vr-hotspotd.service"
systemctl restart vr-hotspotd.service

if [[ "$FIX_AUTOSTART_CONFIG" == "1" ]]; then
  log "Updating persistence config (autostart=$ENABLE_AUTOSTART)..."
  # We use the python environment we just built to safely update the config
  export PYTHONPATH="$INSTALL_DIR"
  if [[ "$ENABLE_AUTOSTART" == "1" ]]; then
      "$VENV_DIR/bin/python3" -c "from vr_hotspotd.config import write_config_file; write_config_file({'autostart': True})" || true
  else
      "$VENV_DIR/bin/python3" -c "from vr_hotspotd.config import write_config_file; write_config_file({'autostart': False})" || true
  fi
fi

log "Backend install complete."
