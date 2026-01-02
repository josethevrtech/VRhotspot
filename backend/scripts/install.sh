#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
VR Hotspot installer

Usage:
  sudo bash backend/scripts/install.sh [options]

Options:
  --bind <host>             Bind host for daemon (default: 127.0.0.1)
  --port <port>             Bind port for daemon (default: 8732)
  --install-dir <path>      Install backend into this directory (default: /var/lib/vr-hotspot/app/backend)
  --enable-autostart        Enable autostart oneshot service (recommended)
  --disable-autostart       Disable autostart oneshot service
  --api-token <token>        Require token for /v1/* endpoints (sets VR_HOTSPOTD_API_TOKEN)
  --no-copy                  Do not copy backend; run in-place (dev only)
  -h, --help                 Show this help
USAGE
}

log() { echo "[install] $*"; }
die() { echo "[install] ERROR: $*" >&2; exit 1; }

BIND="127.0.0.1"
PORT="8732"
DEFAULT_INSTALL_DIR="/var/lib/vr-hotspot/app/backend"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
ENABLE_AUTOSTART="0"
DISABLE_AUTOSTART="0"
API_TOKEN=""
NO_COPY="0"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bind) BIND="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --enable-autostart) ENABLE_AUTOSTART="1"; shift ;;
    --disable-autostart) DISABLE_AUTOSTART="1"; shift ;;
    --api-token) API_TOKEN="${2:-}"; shift 2 ;;
    --no-copy) NO_COPY="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -n "$BIND" ]] || die "--bind is required"
[[ -n "$PORT" ]] || die "--port is required"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be numeric"
[[ "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "--port must be 1..65535"

# Locate repo root and backend dir based on script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_SRC="$REPO_ROOT/backend"

[[ -d "$BACKEND_SRC/vr_hotspotd" ]] || die "Expected $BACKEND_SRC/vr_hotspotd not found. Are you in the right repo?"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found (systemd required)"
command -v python3 >/dev/null 2>&1 || die "python3 not found"

# Create dirs
APP_ROOT="/var/lib/vr-hotspot"
APP_DIR="${APP_ROOT}/app"
BIN_DIR="${APP_ROOT}/bin"
install -d -m 755 /etc/vr-hotspot
install -d -m 755 "$APP_DIR" "$BIN_DIR"
install -d -m 755 "$(dirname "$INSTALL_DIR")"
install -d -m 755 "$INSTALL_DIR"

# Env file (host/port + optional token)
ENV_FILE="/etc/vr-hotspot/env"
EXISTING_TOKEN=""
EXTRA_ENV=""
if [[ -f "$ENV_FILE" ]]; then
  EXISTING_TOKEN="$(grep -E '^VR_HOTSPOTD_API_TOKEN=' "$ENV_FILE" | head -n 1 | cut -d= -f2-)"
  EXTRA_ENV="$(grep -Ev '^(VR_HOTSPOTD_HOST|VR_HOTSPOTD_PORT|VR_HOTSPOTD_API_TOKEN)=' "$ENV_FILE" || true)"
fi

TOKEN_TO_WRITE="$API_TOKEN"
if [[ -z "$TOKEN_TO_WRITE" && -n "$EXISTING_TOKEN" ]]; then
  TOKEN_TO_WRITE="$EXISTING_TOKEN"
fi

log "Writing $ENV_FILE"
orig_umask="$(umask)"
umask 077
{
  echo "VR_HOTSPOTD_HOST=$BIND"
  echo "VR_HOTSPOTD_PORT=$PORT"
  if [[ -n "$TOKEN_TO_WRITE" ]]; then
    echo "VR_HOTSPOTD_API_TOKEN=$TOKEN_TO_WRITE"
  fi
  if [[ -n "$EXTRA_ENV" ]]; then
    printf '%s\n' "$EXTRA_ENV"
  fi
} >"$ENV_FILE"
chmod 600 "$ENV_FILE"
umask "$orig_umask"

# Copy backend into install dir unless --no-copy
if [[ "$NO_COPY" == "1" ]]; then
  log "--no-copy set: service will run in-place from $BACKEND_SRC"
  INSTALL_DIR="$BACKEND_SRC"
else
  log "Copying backend -> $INSTALL_DIR"
  TMP_DIR="${INSTALL_DIR}.tmp.$$"
  rm -rf "$TMP_DIR"
  install -d -m 755 "$TMP_DIR"
  # Copy everything under backend (including vendor/bin)
  cp -a "$BACKEND_SRC/." "$TMP_DIR/"
  rm -rf "$INSTALL_DIR"
  mv "$TMP_DIR" "$INSTALL_DIR"
fi

# Ensure vendor binaries are executable if present
if [[ -d "$INSTALL_DIR/vendor/bin" ]]; then
  chmod +x "$INSTALL_DIR/vendor/bin/"* 2>/dev/null || true
fi

# Install systemd units
SYSTEMD_SRC="${BACKEND_SRC}/systemd"
SYSTEMD_DST="/etc/systemd/system"
UNIT_DAEMON="${SYSTEMD_DST}/vr-hotspotd.service"
UNIT_AUTOSTART="${SYSTEMD_DST}/vr-hotspot-autostart.service"
log "Installing systemd units into $SYSTEMD_DST"
install -m 644 "${SYSTEMD_SRC}/vr-hotspotd.service" "$UNIT_DAEMON"
install -m 644 "${SYSTEMD_SRC}/vr-hotspot-autostart.service" "$UNIT_AUTOSTART"

# Autostart script
AUTOSTART_SH="${BIN_DIR}/vr-hotspot-autostart.sh"
log "Installing $AUTOSTART_SH"
install -m 755 "${BACKEND_SRC}/scripts/vr-hotspot-autostart.sh" "$AUTOSTART_SH"

# Drop-in override for non-default install dir
DROPIN_DIR="/etc/systemd/system/vr-hotspotd.service.d"
DROPIN_FILE="${DROPIN_DIR}/override.conf"
if [[ "$INSTALL_DIR" != "$DEFAULT_INSTALL_DIR" ]]; then
  log "Installing drop-in override for custom install dir"
  install -d -m 755 "$DROPIN_DIR"
  cat >"$DROPIN_FILE" <<EOF
[Service]
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONPATH=$INSTALL_DIR
ExecStartPre=
ExecStartPre=/usr/bin/python3 -m compileall -q $INSTALL_DIR/vr_hotspotd
EOF
else
  if [[ -f "$DROPIN_FILE" ]]; then
    log "Removing drop-in override (default install dir)"
    rm -f "$DROPIN_FILE"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
  fi
fi

# Enable/disable autostart as requested
if [[ "$DISABLE_AUTOSTART" == "1" ]]; then
  log "Disabling autostart (if installed)"
  systemctl disable --now vr-hotspot-autostart.service >/dev/null 2>&1 || true
fi

log "Reloading systemd"
systemctl daemon-reload

log "Enabling and starting vr-hotspotd.service"
systemctl enable --now vr-hotspotd.service

if [[ "$ENABLE_AUTOSTART" == "1" ]]; then
  log "Enabling vr-hotspot-autostart.service"
  systemctl enable --now vr-hotspot-autostart.service
fi

log "Install complete."
log "UI: http://$BIND:$PORT/ui"
log "Status: curl -fsS -H 'X-Api-Token: <token>' http://127.0.0.1:$PORT/v1/status | head"
