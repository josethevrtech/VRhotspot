#!/usr/bin/env bash
# VR Hotspot - Uninstaller

set -e

# --- Configuration ---
APP_NAME="VR Hotspot"
DAEMON_UNIT="vr-hotspotd.service"
AUTOSTART_UNIT="vr-hotspot-autostart.service"
# Backward-compat cleanup only.
LEGACY_SYSTEMD_UNITS=("vr-hotspotd-autostart.service")
INSTALL_ROOT="/var/lib/vr-hotspot"
FIREWALL_LEDGER="$INSTALL_ROOT/firewall-rules.json"
CONFIG_DIR="/etc/vr-hotspot"
SYSTEMD_DIR="/etc/systemd/system"

# --- Colors and Formatting ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# --- Helper Functions ---
print_header() {
    echo -e "${RED}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                    $APP_NAME - Uninstaller                     ║"
    echo -e "╚══════════════════════════════════════════════════════════════════╝${NC}"
}

print_step() { echo -e "${BLUE}${BOLD}▶ $1${NC}"; }
print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_error() { echo -e "${RED}✗ $1${NC}"; }
print_info() { echo -e "${CYAN}ℹ $1${NC}"; }

firewall_ledger_records() {
    python3 - "$FIREWALL_LEDGER" <<'PY'
import json
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
try:
    with path.open("r", encoding="utf-8") as ledger_file:
        ledger = json.load(ledger_file)
    if not isinstance(ledger, dict):
        raise ValueError("firewall ledger is not an object")
    if set(ledger) != {"version", "actions"}:
        raise ValueError("unsupported firewall ledger fields")
    if type(ledger["version"]) is not int or ledger["version"] != 1:
        raise ValueError("unsupported firewall ledger version")
    if not isinstance(ledger["actions"], list):
        raise ValueError("unsupported firewall ledger format")

    rows = []
    for record in reversed(ledger["actions"]):
        if not isinstance(record, dict):
            raise ValueError("firewall ledger action is not an object")
        backend = record.get("backend")
        action = record.get("action")
        if not isinstance(backend, str) or not isinstance(action, str):
            raise ValueError("firewall ledger action has invalid type fields")
        if backend == "firewalld":
            if action not in {"add-port", "add-masquerade", "add-forward"}:
                raise ValueError(f"unsupported firewalld action: {action}")
            expected_fields = {"backend", "action", "scope", "zone"}
            if action == "add-port":
                expected_fields.add("port")
            if set(record) != expected_fields:
                raise ValueError("unsupported firewalld action fields")
            scope = record.get("scope")
            zone = record.get("zone")
            if not isinstance(scope, str) or scope not in {"runtime", "permanent"}:
                raise ValueError(f"unsupported firewalld scope: {scope}")
            if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", zone):
                raise ValueError("invalid firewalld zone")
            value = record.get("port", "")
            if action == "add-port" and value != "8732/tcp":
                raise ValueError(f"unsupported firewalld port: {value}")
            rows.append((backend, action, scope, zone, value))
        elif backend == "ufw":
            if set(record) != {"backend", "action", "rule"}:
                raise ValueError("unsupported UFW action fields")
            rule = record.get("rule")
            if action != "allow" or rule != "8732/tcp":
                raise ValueError(f"unsupported UFW action: {action} {rule}")
            rows.append((backend, action, "", "", rule))
        else:
            raise ValueError(f"unsupported firewall backend: {backend}")

    for row in rows:
        print("|".join(row))
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f"could not read firewall ledger: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
}

rollback_owned_firewall_rules() {
    if [ ! -f "$FIREWALL_LEDGER" ]; then
        print_info "No VRHotspot firewall ledger found; leaving firewall rules unchanged."
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        print_warning "python3 is unavailable; cannot read the VRHotspot firewall ledger."
        return 0
    fi

    local records
    if ! records="$(firewall_ledger_records)"; then
        print_warning "Leaving firewall rules unchanged because the VRHotspot ledger could not be read."
        return 0
    fi

    local backend action scope zone value option output status
    local -a scope_args=()
    while IFS='|' read -r backend action scope zone value; do
        [ -n "$backend" ] || continue
        if [ "$backend" = "firewalld" ]; then
            if ! command -v firewall-cmd >/dev/null 2>&1; then
                print_warning "firewall-cmd is unavailable; skipping recorded $action for zone $zone."
                continue
            fi
            scope_args=()
            [ "$scope" = "permanent" ] && scope_args=(--permanent)
            case "$action" in
                add-port) option="--remove-port=$value" ;;
                add-masquerade) option="--remove-masquerade" ;;
                add-forward) option="--remove-forward" ;;
            esac
            if output="$(firewall-cmd "${scope_args[@]}" --zone "$zone" "$option" 2>&1)"; then
                print_info "Removed recorded firewalld $action from zone $zone ($scope)."
            else
                status=$?
                print_warning "Could not remove recorded firewalld $action from zone $zone ($scope); it may already be absent (exit $status${output:+: $output})."
            fi
        elif [ "$backend" = "ufw" ]; then
            if ! command -v ufw >/dev/null 2>&1; then
                print_warning "ufw is unavailable; skipping recorded allow rule for $value."
                continue
            fi
            if output="$(ufw --force delete allow "$value" 2>&1)"; then
                print_info "Removed recorded UFW allow rule for $value."
            else
                status=$?
                print_warning "Could not remove recorded UFW allow rule for $value; it may already be absent (exit $status${output:+: $output})."
            fi
        fi
    done <<< "$records"
}

# --- Main Uninstallation Logic ---
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This uninstaller requires root privileges. Please run with 'sudo'."
        exit 1
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        print_info "Detected System: $NAME"
    fi
}

main() {
    # Check for force flag
    FORCE=0
    for arg in "$@"; do
        if [[ "$arg" == "-y" || "$arg" == "--yes" || "$arg" == "--force" ]]; then
            FORCE=1
        fi
    done

    [ -t 0 ] && INTERACTIVE=1 || INTERACTIVE=0

    clear
    print_header

    check_root
    detect_os

    print_warning "This will completely remove $APP_NAME and all its configuration."
    if [ "$INTERACTIVE" -eq 1 ] && [ "$FORCE" -eq 0 ]; then
        echo -ne "Are you sure you want to continue? (y/N) "
        read -n 1 -r REPLY || true
        echo
        if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
            print_info "Uninstallation cancelled."
            exit 0
        fi
    fi

    print_step "Stopping and disabling services..."
    local unit
    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        systemctl stop "$unit" &>/dev/null || true
        systemctl disable "$unit" &>/dev/null || true
    done
    print_success "Services stopped and disabled."

    print_step "Rolling back recorded firewall rules..."
    rollback_owned_firewall_rules
    print_success "Recorded firewall rollback complete."

    print_step "Removing systemd service files..."
    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        rm -f "$SYSTEMD_DIR/$unit"
    done
    systemctl daemon-reload
    print_success "Service files removed."

    print_step "Removing all application files and configuration..."
    rm -rf "$INSTALL_ROOT"
    rm -rf "$CONFIG_DIR"
    print_success "Application files and configuration removed."

    echo
    print_success "$APP_NAME has been successfully uninstalled."
    echo
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
