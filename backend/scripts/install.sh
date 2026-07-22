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
  --enable-autostart        Enable hotspot autostart on boot
  --disable-autostart       Disable hotspot autostart on boot
  -h, --help                 Show this help
USAGE
}

log() { echo "[backend-install] $*"; }
die() { echo "[backend-install] ERROR: $*" >&2; exit 1; }

APP_ROOT="/var/lib/vr-hotspot"
FIREWALL_LEDGER="$APP_ROOT/firewall-rules.json"

record_firewall_action() {
  local backend="$1"
  local action="$2"
  local scope="${3:-}"
  local zone="${4:-}"
  local value="${5:-}"

  python3 - "$FIREWALL_LEDGER" "$backend" "$action" "$scope" "$zone" "$value" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

ledger_path = Path(sys.argv[1])
backend, action, scope, zone, value = sys.argv[2:]

if backend == "firewalld":
    if action not in {"add-port", "add-masquerade", "add-forward"}:
        raise SystemExit(f"unsupported firewalld action: {action}")
    if scope not in {"runtime", "permanent"}:
        raise SystemExit(f"unsupported firewalld scope: {scope}")
    if not zone:
        raise SystemExit("firewalld action requires a zone")
    record = {
        "backend": backend,
        "action": action,
        "scope": scope,
        "zone": zone,
    }
    if action == "add-port":
        if value != "8732/tcp":
            raise SystemExit(f"unsupported firewalld port: {value}")
        record["port"] = value
elif backend == "ufw":
    if action != "allow" or value != "8732/tcp":
        raise SystemExit(f"unsupported UFW action: {action} {value}")
    record = {"backend": backend, "action": action, "rule": value}
else:
    raise SystemExit(f"unsupported firewall backend: {backend}")

ledger_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
if ledger_path.exists():
    with ledger_path.open("r", encoding="utf-8") as ledger_file:
        ledger = json.load(ledger_file)
    if ledger.get("version") != 1 or not isinstance(ledger.get("actions"), list):
        raise SystemExit("unsupported firewall ledger format")
else:
    ledger = {"version": 1, "actions": []}

if record not in ledger["actions"]:
    ledger["actions"].append(record)

fd, temporary_name = tempfile.mkstemp(
    prefix=f".{ledger_path.name}.", dir=ledger_path.parent
)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as ledger_file:
        json.dump(ledger, ledger_file, indent=2, sort_keys=True)
        ledger_file.write("\n")
        ledger_file.flush()
        os.fsync(ledger_file.fileno())
    os.replace(temporary_name, ledger_path)
    os.chmod(ledger_path, 0o600)
except BaseException:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
PY
}

ensure_firewalld_action() {
  local action="$1"
  local scope="$2"
  local zone="$3"
  local value="${4:-}"
  local query_option add_option remove_option description
  local -a scope_args=()

  if [[ ! "$zone" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    log "Warning: skipping firewalld action with invalid zone '$zone'"
    return 0
  fi

  case "$action" in
    add-port)
      query_option="--query-port=$value"
      add_option="--add-port=$value"
      remove_option="--remove-port=$value"
      description="$value in zone $zone ($scope)"
      ;;
    add-masquerade)
      query_option="--query-masquerade"
      add_option="--add-masquerade"
      remove_option="--remove-masquerade"
      description="masquerade in zone $zone ($scope)"
      ;;
    add-forward)
      query_option="--query-forward"
      add_option="--add-forward"
      remove_option="--remove-forward"
      description="forward in zone $zone ($scope)"
      ;;
    *)
      log "Warning: skipping unsupported firewalld action '$action'"
      return 0
      ;;
  esac

  if [[ "$scope" == "permanent" ]]; then
    scope_args=(--permanent)
  elif [[ "$scope" != "runtime" ]]; then
    log "Warning: skipping firewalld action with invalid scope '$scope'"
    return 0
  fi

  local query_status
  if firewall-cmd "${scope_args[@]}" --zone "$zone" "$query_option" >/dev/null 2>&1; then
    return 0
  else
    query_status=$?
  fi

  if [[ "$query_status" -ne 1 ]]; then
    log "Warning: could not verify ownership for firewalld $description; leaving it unchanged"
    return 0
  fi

  if ! firewall-cmd "${scope_args[@]}" --zone "$zone" "$add_option" >/dev/null 2>&1; then
    log "Warning: failed to enable firewalld $description"
    return 0
  fi

  if record_firewall_action "firewalld" "$action" "$scope" "$zone" "$value"; then
    return 0
  fi

  log "Warning: could not record firewalld $description; reverting the untracked change"
  if ! firewall-cmd "${scope_args[@]}" --zone "$zone" "$remove_option" >/dev/null 2>&1; then
    log "Warning: failed to revert untracked firewalld $description"
  fi
}

enable_firewalld_uplink_forwarding() {
  if ! command -v firewall-cmd >/dev/null 2>&1; then
    log "firewalld not installed; skipping uplink forwarding setup"
    return 0
  fi
  if ! firewall-cmd --state >/dev/null 2>&1; then
    log "firewalld not running; skipping uplink forwarding setup"
    return 0
  fi

  local uplink
  uplink="$(ip route show default 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  if [[ -z "$uplink" ]]; then
    log "no default route interface detected; skipping uplink forwarding setup"
    return 0
  fi

  local zone
  zone="$(firewall-cmd --get-zone-of-interface="$uplink" 2>/dev/null | head -n1 | tr -d '\r')"
  if [[ -z "$zone" ]]; then
    zone="$(firewall-cmd --get-default-zone 2>/dev/null | head -n1 | tr -d '\r')"
  fi
  if [[ -z "$zone" ]]; then
    log "unable to determine firewalld zone for uplink $uplink; skipping"
    return 0
  fi

  log "Ensuring firewalld forwarding for uplink $uplink (zone=$zone)"
  ensure_firewalld_action "add-masquerade" "runtime" "$zone"
  ensure_firewalld_action "add-forward" "runtime" "$zone"
  ensure_firewalld_action "add-masquerade" "permanent" "$zone"
  ensure_firewalld_action "add-forward" "permanent" "$zone"
}

# Allow focused tests to source the firewall helpers without running installation.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

DEFAULT_INSTALL_DIR="/var/lib/vr-hotspot/app"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
ENABLE_AUTOSTART="0"
FIX_AUTOSTART_CONFIG="0"
DAEMON_UNIT="vr-hotspotd.service"
AUTOSTART_UNIT="vr-hotspot-autostart.service"
# Backward-compat cleanup only.
LEGACY_SYSTEMD_UNITS=("vr-hotspotd-autostart.service")

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
VENV_DIR="$APP_ROOT/venv"
BIN_DIR="$APP_ROOT/bin"
SYSTEMD_DST="/etc/systemd/system"
install -d -m 755 "$APP_ROOT"
install -d -m 755 /etc/vr-hotspot
# Ensure env file exists so systemd doesn't fail on missing EnvironmentFile.
touch /etc/vr-hotspot/env
chmod 600 /etc/vr-hotspot/env || true

log "Copying application files -> $INSTALL_DIR"
# Copy source code
mkdir -p "$INSTALL_DIR"
cp -r "$BACKEND_SRC/../." "$INSTALL_DIR/"

# Ensure Web UI assets are refreshed in the install directory.
if [[ ! -d "$REPO_ROOT/assets" ]]; then
  die "Assets directory not found at $REPO_ROOT/assets"
fi
log "Syncing Web UI assets -> $INSTALL_DIR/assets"
install -d -m 755 "$INSTALL_DIR/assets"
cp -a "$REPO_ROOT/assets/." "$INSTALL_DIR/assets/"

# Ensure vendor binaries are executable if present
if [[ -d "$INSTALL_DIR/backend/vendor/bin" ]]; then
  chmod +x "$INSTALL_DIR/backend/vendor/bin/"* 2>/dev/null || true
fi

# Ensure Bazzite prefers bundled hostapd/dnsmasq (system hostapd has been unstable on some installs).
OS_ID=""
OS_ID_LIKE=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-}"
  OS_ID_LIKE="${ID_LIKE:-}"
fi
if [[ "$OS_ID" == "bazzite" ]]; then
  log "Bazzite detected: enforcing bundled vendor binaries in /etc/vr-hotspot/env"
  install -d -m 755 /etc/vr-hotspot
  touch /etc/vr-hotspot/env
  if grep -q "^VR_HOTSPOT_FORCE_VENDOR_BIN=" /etc/vr-hotspot/env; then
    sed -i 's/^VR_HOTSPOT_FORCE_VENDOR_BIN=.*/VR_HOTSPOT_FORCE_VENDOR_BIN=1/' /etc/vr-hotspot/env
  else
    echo "VR_HOTSPOT_FORCE_VENDOR_BIN=1" >> /etc/vr-hotspot/env
  fi
fi

is_truthy() {
  case "$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

install_autostart_helper() {
  local helper_src="$BACKEND_SRC/scripts/vr-hotspot-autostart.sh"
  local wait_src="$BACKEND_SRC/scripts/wait-healthy.sh"

  [[ -f "$helper_src" ]] || die "Missing helper script: $helper_src"
  [[ -f "$wait_src" ]] || die "Missing health wait script: $wait_src"

  log "Installing autostart helper scripts into $BIN_DIR"
  install -d -m 755 "$BIN_DIR"
  install -m 755 "$helper_src" "$BIN_DIR/vr-hotspot-autostart.sh"
  install -m 755 "$wait_src" "$BIN_DIR/wait-healthy.sh"
}

install_cli_launcher() {
  local cli_src="$VENV_DIR/bin/vr-hotspot"
  local cli_dst="$BIN_DIR/vr-hotspot"

  [[ -x "$cli_src" ]] || die "Installed CLI not found: $cli_src"
  log "Installing read-only CLI launcher -> $cli_dst"
  install -d -m 755 "$BIN_DIR"
  ln -sfn "$cli_src" "$cli_dst"
}

install_systemd_units() {
  local template_dir="$BACKEND_SRC/systemd"
  local unit template tmp

  log "Installing systemd units into $SYSTEMD_DST"
  for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT"; do
    template="$template_dir/$unit"
    [[ -f "$template" ]] || die "Missing systemd unit template: $template"

    tmp="$(mktemp)"
    cp "$template" "$tmp"
    sed -i \
      -e "s|/var/lib/vr-hotspot/app|$INSTALL_DIR|g" \
      -e "s|/usr/bin/python3 -m vr_hotspotd.main|$VENV_DIR/bin/python -m vr_hotspotd.main|g" \
      "$tmp"
    install -m 644 "$tmp" "$SYSTEMD_DST/$unit"
    rm -f "$tmp"
  done
}

cleanup_legacy_systemd_units() {
  local unit
  for unit in "${LEGACY_SYSTEMD_UNITS[@]}"; do
    systemctl disable --now "$unit" &>/dev/null || true
    rm -f "$SYSTEMD_DST/$unit"
  done
}

sync_autostart_service_state() {
  local autostart_enabled="$ENABLE_AUTOSTART"

  if [[ "$FIX_AUTOSTART_CONFIG" != "1" ]]; then
    autostart_enabled="$(
      "$VENV_DIR/bin/python3" -c \
        "from vr_hotspotd.config import load_config; print('1' if bool(load_config().get('autostart')) else '0')" \
        2>/dev/null || echo "0"
    )"
  fi

  if is_truthy "$autostart_enabled"; then
    log "Enabling $AUTOSTART_UNIT (autostart enabled)"
    systemctl enable "$AUTOSTART_UNIT"
  else
    log "Disabling $AUTOSTART_UNIT (autostart disabled)"
    systemctl disable --now "$AUTOSTART_UNIT" &>/dev/null || true
  fi
}

if [[ "$OS_ID" == "bazzite" || "$OS_ID" == "fedora" || "$OS_ID" == "endeavouros" || "$OS_ID_LIKE" == *"fedora"* ]]; then
  enable_firewalld_uplink_forwarding
fi

# Create Python virtual environment
log "Creating Python virtual environment at $VENV_DIR..."
python3 -m venv "$VENV_DIR"
log "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --no-cache-dir -U pip &>/dev/null
"$VENV_DIR/bin/pip" install --no-cache-dir "$INSTALL_DIR" &>/dev/null

install_cli_launcher
install_autostart_helper
install_systemd_units
cleanup_legacy_systemd_units

log "Reloading systemd"
systemctl daemon-reload

log "Enabling $DAEMON_UNIT (daemon always enabled)"
systemctl enable "$DAEMON_UNIT"

if [[ "$FIX_AUTOSTART_CONFIG" == "1" ]]; then
  log "Updating persistence config (autostart=$ENABLE_AUTOSTART)"
  if [[ "$ENABLE_AUTOSTART" == "1" ]]; then
      "$VENV_DIR/bin/python3" -c "from vr_hotspotd.config import write_config_file; write_config_file({'autostart': True})" || true
  else
      "$VENV_DIR/bin/python3" -c "from vr_hotspotd.config import write_config_file; write_config_file({'autostart': False})" || true
  fi
fi

sync_autostart_service_state

log "Starting $DAEMON_UNIT (after asset sync)"
systemctl restart "$DAEMON_UNIT"

log "Backend install complete."
