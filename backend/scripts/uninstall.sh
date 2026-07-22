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
APP_ROOT="/var/lib/vr-hotspot"
FIREWALL_LEDGER="$APP_ROOT/firewall-rules.json"
CONFIG_DIR="/etc/vr-hotspot"

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
    if [[ ! -f "$FIREWALL_LEDGER" ]]; then
        log "No VRHotspot firewall ledger found; leaving firewall rules unchanged"
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log "Warning: python3 is unavailable; cannot read the VRHotspot firewall ledger"
        return 0
    fi

    local records
    if ! records="$(firewall_ledger_records)"; then
        log "Warning: leaving firewall rules unchanged because the VRHotspot ledger could not be read"
        return 0
    fi

    local backend action scope zone value option output status
    local -a scope_args=()
    while IFS='|' read -r backend action scope zone value; do
        [[ -n "$backend" ]] || continue
        if [[ "$backend" == "firewalld" ]]; then
            if ! command -v firewall-cmd >/dev/null 2>&1; then
                log "Warning: firewall-cmd is unavailable; skipping recorded $action for zone $zone"
                continue
            fi
            scope_args=()
            [[ "$scope" == "permanent" ]] && scope_args=(--permanent)
            case "$action" in
                add-port) option="--remove-port=$value" ;;
                add-masquerade) option="--remove-masquerade" ;;
                add-forward) option="--remove-forward" ;;
            esac
            if output="$(firewall-cmd "${scope_args[@]}" --zone "$zone" "$option" 2>&1)"; then
                log "Removed recorded firewalld $action from zone $zone ($scope)"
            else
                status=$?
                log "Warning: could not remove recorded firewalld $action from zone $zone ($scope); it may already be absent (exit $status${output:+: $output})"
            fi
        elif [[ "$backend" == "ufw" ]]; then
            if ! command -v ufw >/dev/null 2>&1; then
                log "Warning: ufw is unavailable; skipping recorded allow rule for $value"
                continue
            fi
            if output="$(ufw --force delete allow "$value" 2>&1)"; then
                log "Removed recorded UFW allow rule for $value"
            else
                status=$?
                log "Warning: could not remove recorded UFW allow rule for $value; it may already be absent (exit $status${output:+: $output})"
            fi
        fi
    done <<< "$records"
}

main() {
    if [[ "${EUID}" -ne 0 ]]; then
        die "This script must be run as root."
    fi

    log "Stopping and disabling services..."
    local unit
    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        systemctl disable --now "$unit" &>/dev/null || true
    done

    log "Rolling back recorded firewall rules..."
    rollback_owned_firewall_rules

    log "Removing systemd unit files..."
    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        rm -f "$SYSTEMD_DIR/$unit"
    done
    rm -rf "$SYSTEMD_DIR/$DAEMON_UNIT.d"
    systemctl daemon-reload

    log "Removing application and configuration files..."
    rm -rf "$APP_ROOT"
    rm -rf "$CONFIG_DIR"

    log "Uninstallation complete."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
