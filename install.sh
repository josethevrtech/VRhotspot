#!/usr/bin/env bash
# VR Hotspot - Enhanced Interactive Installer

set -e

# --- Configuration ---
APP_NAME="VR Hotspot"
DAEMON_UNIT="vr-hotspotd.service"
AUTOSTART_UNIT="vr-hotspot-autostart.service"
# Backward-compat cleanup only.
LEGACY_SYSTEMD_UNITS=("vr-hotspotd-autostart.service")
INSTALL_ROOT="/var/lib/vr-hotspot"
APP_DIR="$INSTALL_ROOT/app"
FIREWALL_LEDGER="$INSTALL_ROOT/firewall-rules.json"
CONFIG_DIR="/etc/vr-hotspot"
ENV_FILE="$CONFIG_DIR/env"
SYSTEMD_DIR="/etc/systemd/system"
FLATPAK_COMPANION_APP_ID="io.github.josethevrtech.VRhotspot"
FLATPAK_COMPANION_MANIFEST_RELATIVE="packaging/flatpak/io.github.josethevrtech.VRhotspot.json"

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
    echo -e "${CYAN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║            $APP_NAME - Enhanced Interactive Installer             ║"
    echo -e "╚══════════════════════════════════════════════════════════════════╝${NC}"
}

print_step() { echo -e "${BLUE}${BOLD}▶ $1${NC}"; }
print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_error() { echo -e "${RED}✗ $1${NC}"; }
print_info() { echo -e "${CYAN}ℹ $1${NC}"; }

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
        print_warning "Skipping firewalld action with invalid zone '$zone'."
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
            print_warning "Skipping unsupported firewalld action '$action'."
            return 0
            ;;
    esac

    if [ "$scope" = "permanent" ]; then
        scope_args=(--permanent)
    elif [ "$scope" != "runtime" ]; then
        print_warning "Skipping firewalld action with invalid scope '$scope'."
        return 0
    fi

    local query_status
    if firewall-cmd "${scope_args[@]}" --zone "$zone" "$query_option" >/dev/null 2>&1; then
        return 0
    else
        query_status=$?
    fi

    if [ "$query_status" -ne 1 ]; then
        print_warning "Could not verify ownership for firewalld $description; leaving it unchanged."
        return 0
    fi

    if ! firewall-cmd "${scope_args[@]}" --zone "$zone" "$add_option" >/dev/null 2>&1; then
        print_warning "Failed to enable firewalld $description."
        return 0
    fi

    if record_firewall_action "firewalld" "$action" "$scope" "$zone" "$value"; then
        return 0
    fi

    print_warning "Could not record firewalld $description; reverting the untracked change."
    if ! firewall-cmd "${scope_args[@]}" --zone "$zone" "$remove_option" >/dev/null 2>&1; then
        print_warning "Failed to revert untracked firewalld $description."
    fi
}

ensure_ufw_api_port() {
    local added_rules add_output

    if ! added_rules="$(ufw show added 2>/dev/null)"; then
        print_warning "Could not verify existing UFW rules; leaving UFW unchanged."
        return 0
    fi
    if printf '%s\n' "$added_rules" | grep -Eq '^ufw[[:space:]]+allow[[:space:]]+8732/tcp([[:space:]]|$)'; then
        return 0
    fi

    if ! add_output="$(ufw allow 8732/tcp 2>&1)"; then
        print_warning "Failed to add UFW rule for 8732/tcp."
        return 0
    fi
    if [[ "${add_output,,}" == *"existing rule"* ]]; then
        print_info "UFW already allows 8732/tcp; leaving the unowned rule unchanged."
        return 0
    fi

    if record_firewall_action "ufw" "allow" "" "" "8732/tcp"; then
        return 0
    fi

    print_warning "Could not record the UFW 8732/tcp rule; reverting the untracked change."
    if ! ufw --force delete allow 8732/tcp >/dev/null 2>&1; then
        print_warning "Failed to revert the untracked UFW 8732/tcp rule."
    fi
}

open_remote_access_firewall() {
    if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
        print_info "Opening port 8732/tcp in firewall..."
        local default_zone zone scope seen_zone
        local -a zones=(public FedoraWorkstation)
        default_zone="$(firewall-cmd --get-default-zone 2>/dev/null | head -n1 | tr -d '\r')"
        if [ -n "$default_zone" ]; then
            zones=("$default_zone" "${zones[@]}")
        else
            print_warning "Unable to determine the default firewalld zone; skipping that zone."
        fi

        local -a unique_zones=()
        for zone in "${zones[@]}"; do
            seen_zone=0
            local existing_zone
            for existing_zone in "${unique_zones[@]}"; do
                if [ "$existing_zone" = "$zone" ]; then
                    seen_zone=1
                    break
                fi
            done
            [ "$seen_zone" -eq 1 ] || unique_zones+=("$zone")
        done

        for zone in "${unique_zones[@]}"; do
            for scope in runtime permanent; do
                ensure_firewalld_action "add-port" "$scope" "$zone" "8732/tcp"
            done
        done
    elif command -v ufw &>/dev/null; then
        print_info "Opening port 8732/tcp in UFW..."
        ensure_ufw_api_port
    else
        print_warning "No supported firewall manager found (firewalld/ufw). Please manually open TCP port 8732."
    fi
}

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

usage() {
    cat <<'USAGE'
VR Hotspot Installer

Usage:
  sudo ./install.sh [options]
  curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
  sudo bash /tmp/vrhotspot-install.sh [options]

Options:
  --interactive       Prompt for installer choices when a real TTY is available
  --non-interactive   Disable prompts and use safe defaults
  --yes, -y           Alias for --non-interactive
  --install-flatpak-companion
                      Build and install the prototype Flatpak companion for the invoking user
  --check-os          Detect OS and print dependency plan only
  --no-clear          Do not clear the terminal before output
  -h, --help          Show this help
USAGE
}

is_truthy() {
    case "$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on|y) return 0 ;;
        *) return 1 ;;
    esac
}

is_ci_environment() {
    is_truthy "${CI:-}" || \
        is_truthy "${GITHUB_ACTIONS:-}" || \
        is_truthy "${GITLAB_CI:-}" || \
        is_truthy "${BUILDKITE:-}" || \
        is_truthy "${TF_BUILD:-}"
}

prompt_tty_available() {
    [ -t 0 ] && [ -t 1 ] && [ -r /dev/tty ]
}

resolve_interactive_mode() {
    local requested_mode="${1:-auto}"

    INTERACTIVE=0
    NON_INTERACTIVE_REASON=""

    if is_ci_environment; then
        NON_INTERACTIVE_REASON="CI environment detected"
        return 0
    fi

    case "$requested_mode" in
        interactive)
            if prompt_tty_available; then
                INTERACTIVE=1
            else
                NON_INTERACTIVE_REASON="--interactive requested, but no usable terminal was detected"
            fi
            ;;
        non-interactive)
            NON_INTERACTIVE_REASON="requested by command-line flag"
            ;;
        auto)
            if prompt_tty_available; then
                INTERACTIVE=1
            else
                NON_INTERACTIVE_REASON="no usable terminal detected"
            fi
            ;;
        *)
            print_error "Internal error: unknown interactive mode '$requested_mode'."
            exit 1
            ;;
    esac
}

interactive_read() {
    if [ -c /dev/tty ]; then
        read -r "$@" < /dev/tty
    else
        read -r "$@"
    fi
}

prompt_yes_no() {
    local prompt="$1"
    local default_answer="$2"
    local reply normalized
    while true; do
        interactive_read -r -p "$prompt" reply || true
        if [[ -z "$reply" ]]; then
            reply="$default_answer"
        fi
        normalized="$(echo "$reply" | tr '[:upper:]' '[:lower:]')"
        case "$normalized" in
            y|yes)
                return 0
                ;;
            n|no)
                return 1
                ;;
            *)
                print_warning "Please answer 'y' or 'n'."
                ;;
        esac
    done
}

# --- Optional Flatpak Companion ---
configure_flatpak_companion_install() {
    INSTALL_FLATPAK_COMPANION="n"

    if [ "${FLATPAK_COMPANION_OPT_IN:-0}" -eq 1 ]; then
        INSTALL_FLATPAK_COMPANION="y"
        print_info "Flatpak companion install explicitly requested."
    elif [ "$INTERACTIVE" -eq 1 ]; then
        if prompt_yes_no "Install the Flatpak companion app? (y/N) " "n"; then
            INSTALL_FLATPAK_COMPANION="y"
        else
            print_info "Flatpak companion app install skipped."
        fi
    else
        print_info "Flatpak companion app install skipped (default: No)."
    fi
}

resolve_flatpak_companion_user() {
    local candidate current_uid candidate_uid candidate_gid
    current_uid="$(id -u)"

    if [ "$current_uid" -eq 0 ]; then
        candidate="${SUDO_USER:-}"
        if [ -z "$candidate" ] || [ "$candidate" = "root" ]; then
            print_warning "Cannot determine a non-root invoking user for the user-scoped Flatpak install."
            print_info "Run the installer through sudo from the desktop user that should own the companion app."
            return 1
        fi
    else
        candidate="$(id -un)"
    fi

    if ! candidate_uid="$(id -u "$candidate" 2>/dev/null)" ||
       ! candidate_gid="$(id -g "$candidate" 2>/dev/null)"; then
        print_warning "Cannot resolve the Flatpak companion install user '$candidate'."
        return 1
    fi
    if [ "$candidate_uid" -eq 0 ]; then
        print_warning "Refusing to install the user-scoped Flatpak companion for root."
        return 1
    fi

    FLATPAK_COMPANION_USER="$candidate"
    FLATPAK_COMPANION_UID="$candidate_uid"
    FLATPAK_COMPANION_GID="$candidate_gid"
}

run_flatpak_companion_as_user() {
    local current_uid
    current_uid="$(id -u)"

    # Remove daemon credential variables without inspecting their values.
    if [ "$current_uid" -eq "$FLATPAK_COMPANION_UID" ]; then
        env -u VR_HOTSPOTD_API_TOKEN -u API_TOKEN "$@"
    else
        sudo -H -u "$FLATPAK_COMPANION_USER" -- \
            env -u VR_HOTSPOTD_API_TOKEN -u API_TOKEN "$@"
    fi
}

check_flatpak_companion_prerequisites() {
    local -a missing_tools=()

    command -v flatpak >/dev/null 2>&1 || missing_tools+=("flatpak")
    if command -v flatpak-builder >/dev/null 2>&1; then
        FLATPAK_BUILDER_BIN="$(command -v flatpak-builder)"
    else
        missing_tools+=("flatpak-builder")
    fi

    if [ "${#missing_tools[@]}" -gt 0 ]; then
        print_warning "Flatpak companion install skipped; missing required tool(s): ${missing_tools[*]}."
        print_info "Install the missing tools and the GNOME 50 runtime/SDK, then rerun with --install-flatpak-companion."
        return 1
    fi

    FLATPAK_COMPANION_MANIFEST="$TEMP_INSTALL_DIR/$FLATPAK_COMPANION_MANIFEST_RELATIVE"
    if [ ! -f "$FLATPAK_COMPANION_MANIFEST" ]; then
        print_warning "Flatpak companion install skipped; manifest not found at $FLATPAK_COMPANION_MANIFEST."
        return 1
    fi

    if ! resolve_flatpak_companion_user; then
        return 1
    fi

    if [ "$(id -u)" -ne "$FLATPAK_COMPANION_UID" ] &&
       ! command -v sudo >/dev/null 2>&1; then
        print_warning "Flatpak companion install skipped; sudo is required to install for $FLATPAK_COMPANION_USER."
        return 1
    fi
}

cleanup_flatpak_companion_build_root() {
    local build_root="$1"

    case "$build_root" in
        /tmp/vrhotspot-flatpak-companion.*)
            rm -rf -- "$build_root"
            ;;
        *)
            print_warning "Refusing to clean unexpected Flatpak companion build path: $build_root"
            return 1
            ;;
    esac
}

build_and_install_flatpak_companion() {
    local build_root build_dir state_dir build_log current_uid build_status

    if ! build_root="$(mktemp -d /tmp/vrhotspot-flatpak-companion.XXXXXX)"; then
        print_warning "Could not create a temporary Flatpak companion build directory."
        return 1
    fi
    build_dir="$build_root/build"
    state_dir="$build_root/state"
    build_log="$build_root/flatpak-builder.log"
    current_uid="$(id -u)"

    chmod 700 "$build_root"
    if [ "$current_uid" -ne "$FLATPAK_COMPANION_UID" ] &&
       ! chown "$FLATPAK_COMPANION_UID:$FLATPAK_COMPANION_GID" "$build_root"; then
        print_warning "Could not prepare the Flatpak companion build directory for $FLATPAK_COMPANION_USER."
        cleanup_flatpak_companion_build_root "$build_root"
        return 1
    fi

    print_step "Building and installing the Flatpak companion app for $FLATPAK_COMPANION_USER..."
    build_status=0
    if run_flatpak_companion_as_user \
        "$FLATPAK_BUILDER_BIN" \
        --user \
        --install \
        --assumeyes \
        --force-clean \
        --delete-build-dirs \
        --state-dir="$state_dir" \
        "$build_dir" \
        "$FLATPAK_COMPANION_MANIFEST" >"$build_log" 2>&1; then
        print_success "Flatpak companion app ($FLATPAK_COMPANION_APP_ID) installed for $FLATPAK_COMPANION_USER."
    else
        build_status=$?
        print_warning "Flatpak companion build/install failed (exit $build_status)."
        if [ -s "$build_log" ]; then
            print_info "Last Flatpak builder messages:"
            tail -n 20 "$build_log" | cut -c 1-500
        fi
        print_info "The daemon installation remains complete. This installer does not add Flatpak remotes."
        print_info "Install the GNOME 50 runtime/SDK from a trusted configured source, then retry with --install-flatpak-companion."
    fi

    if ! cleanup_flatpak_companion_build_root "$build_root"; then
        print_warning "Temporary Flatpak companion build files may remain at $build_root."
    fi
    return "$build_status"
}

install_flatpak_companion_if_requested() {
    if [ "${INSTALL_FLATPAK_COMPANION:-n}" != "y" ]; then
        return 0
    fi

    if ! check_flatpak_companion_prerequisites; then
        print_warning "Continuing without the optional Flatpak companion app."
        return 0
    fi
    if ! build_and_install_flatpak_companion; then
        print_warning "Continuing without the optional Flatpak companion app."
    fi
    return 0
}

_fix_apt_code_repo_signedby_conflict() {
    local marker="packages.microsoft.com/repos/code"
    local ts
    ts="$(date +%s)"
    local -a hits=()
    local f

    if [ -f /etc/apt/sources.list ] && grep -Eq "^[[:space:]]*deb(-src)?[[:space:]].*${marker}" /etc/apt/sources.list; then
        hits+=("/etc/apt/sources.list")
    fi

    shopt -s nullglob
    for f in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        if [ "${f##*.}" = "list" ]; then
            if grep -Eq "^[[:space:]]*deb(-src)?[[:space:]].*${marker}" "$f"; then
                hits+=("$f")
            fi
        else
            if grep -Eq "^[[:space:]]*URIs:[[:space:]]*https?://packages.microsoft.com/repos/code" "$f"; then
                hits+=("$f")
            fi
        fi
    done
    shopt -u nullglob

    if [ "${#hits[@]}" -le 1 ]; then
        return 1
    fi

    print_warning "Detected duplicate VS Code apt source entries with different Signed-By values."
    print_info "Keeping: ${hits[0]}"

    local keep
    keep="${hits[0]}"
    local changed=0
    for f in "${hits[@]}"; do
        if [ "$f" = "$keep" ]; then
            continue
        fi

        cp -a "$f" "${f}.vrhotspot.bak.${ts}" || true
        if [ "$f" = "/etc/apt/sources.list" ] || [ "${f##*.}" = "list" ]; then
            sed -i -E \
                '/^[[:space:]]*deb(-src)?[[:space:]].*packages\.microsoft\.com\/repos\/code/s/^/# disabled-by-vr-hotspot /' \
                "$f"
            print_info "Commented conflicting entry in $f (backup: ${f}.vrhotspot.bak.${ts})"
        else
            if grep -Eiq '^[[:space:]]*Enabled:' "$f"; then
                sed -i -E 's/^[[:space:]]*Enabled:[[:space:]]*.*/Enabled: no/I' "$f"
            else
                # Deb822 source file without Enabled key: prepend a global disable flag.
                local tmpf
                tmpf="$(mktemp)"
                {
                    echo "Enabled: no"
                    cat "$f"
                } >"$tmpf"
                mv "$tmpf" "$f"
            fi
            print_info "Disabled conflicting source in-place: $f (backup: ${f}.vrhotspot.bak.${ts})"
        fi
        changed=1
    done

    [ "$changed" -eq 1 ]
}

_apt_update_with_retry() {
    local log_file
    log_file="$(mktemp)"
    if apt-get update -qq >"$log_file" 2>&1; then
        rm -f "$log_file"
        return 0
    fi

    if grep -q "Conflicting values set for option Signed-By" "$log_file" && \
       grep -q "packages.microsoft.com/repos/code" "$log_file"; then
        print_warning "apt update failed due to VS Code repo Signed-By conflict."
        if _fix_apt_code_repo_signedby_conflict; then
            print_info "Retrying apt update after source cleanup..."
            if apt-get update -qq; then
                rm -f "$log_file"
                return 0
            fi
        fi
    fi

    cat "$log_file" >&2
    rm -f "$log_file"
    return 1
}

_rpm_ostree_already_requested_output() {
    local output_lower
    output_lower="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$output_lower" == *"already requested"* ]] && \
        [[ "$output_lower" == *"package/capability"* ]] && \
        [[ "$output_lower" == *"rpm-ostree"* ]]
}

_run_rpm_ostree_install() {
    local output status errexit_was_set=0

    case "$-" in
        *e*) errexit_was_set=1 ;;
    esac

    set +e
    output="$(rpm-ostree install "$@" 2>&1)"
    status=$?
    if [ "$errexit_was_set" -eq 1 ]; then
        set -e
    else
        set +e
    fi

    if [ -n "$output" ]; then
        if [ "$status" -ne 0 ]; then
            printf '%s\n' "$output" >&2
        else
            printf '%s\n' "$output"
        fi
    fi

    if [ "$status" -ne 0 ] && _rpm_ostree_already_requested_output "$output"; then
        print_warning "rpm-ostree reports that this package is already requested. Reboot your system, then rerun the VR Hotspot installer."
        return 2
    fi

    return "$status"
}

# --- Pre-flight & Cleanup ---
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This installer requires root privileges. Please run with 'sudo'."
        exit 1
    fi
}

cleanup_previous_install() {
    print_step "Checking for existing installation..."
    if [ ! -d "$INSTALL_ROOT" ] && ! systemctl list-unit-files | grep -Fq "$DAEMON_UNIT"; then
        print_success "No existing installation found."
        return 0
    fi

    print_warning "Existing $APP_NAME installation detected."
    if [ "$INTERACTIVE" -eq 1 ]; then
        if ! prompt_yes_no "Perform a full cleanup of the previous version? (Y/n) " "y"; then
            print_error "Cannot proceed with an existing installation. Aborting."
            exit 1
        fi
    fi

    print_info "Cleaning up previous installation..."
    local unit
    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        systemctl stop "$unit" &>/dev/null || true
        systemctl disable "$unit" &>/dev/null || true
    done
    pkill -f "vr_hotspotd/main.py" &>/dev/null || true

    print_info "Rolling back recorded firewall rules..."
    rollback_owned_firewall_rules

    for unit in "$DAEMON_UNIT" "$AUTOSTART_UNIT" "${LEGACY_SYSTEMD_UNITS[@]}"; do
        rm -f "$SYSTEMD_DIR/$unit"
    done
    systemctl daemon-reload

    print_info "Removing files and directories..."
    rm -rf "$INSTALL_ROOT" "$CONFIG_DIR" "/run/vr-hotspot" "/tmp/vr-hotspot-*"
    print_success "Cleanup complete."
}

# --- Installation Steps ---
detect_os() {
    print_step "Detecting Operating System..."
    if [ -n "${VR_HOTSPOT_OS_ID:-}" ]; then
        OS_ID="${VR_HOTSPOT_OS_ID}"
        OS_NAME="${VR_HOTSPOT_OS_NAME:-$OS_ID}"
        OS_ID_LIKE="${VR_HOTSPOT_OS_ID_LIKE:-}"
    elif [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="$ID"
        OS_NAME="$NAME"
        OS_ID_LIKE="${ID_LIKE:-}"
    else
        print_error "Cannot detect OS (/etc/os-release not found)."
        exit 1
    fi
    case "$OS_ID" in
        steamos|cachyos|arch|endeavouros) PKG_MANAGER="pacman" ;;
        ubuntu|debian|pop) PKG_MANAGER="apt" ;;
        fedora) PKG_MANAGER="dnf" ;;
        bazzite) PKG_MANAGER="rpm-ostree" ;;
        *)
            print_error "Unsupported OS: $OS_ID. Please install dependencies manually."
            exit 1
            ;;
    esac
    print_success "Detected $OS_NAME ($PKG_MANAGER)."
    if [ "$OS_ID" = "bazzite" ]; then
        print_info "Bazzite support policy: supported through the rpm-ostree path with bundled hostapd/dnsmasq."
        print_info "Missing base tools may be layered; if live application is unavailable, reboot and rerun the installer."
    fi
}

calculate_dependency_list() {
    DEPENDENCIES=()
    case "$PKG_MANAGER" in
        pacman)
            DEPENDENCIES=(python python-pip iw iproute2)
            if [[ "$OS_ID" != "steamos" ]]; then
                DEPENDENCIES+=("dnsmasq" "iptables")
            fi
            ;;
        apt)
            DEPENDENCIES=(python3 python3-pip python3-venv iw iproute2 iptables hostapd dnsmasq)
            ;;
        dnf)
            DEPENDENCIES=(python3 python3-pip iw iproute iptables)
            ;;
        rpm-ostree)
            DEPENDENCIES=(python3 python3-pip iw iproute iptables)
            if [ "$OS_ID" != "bazzite" ]; then
                local force_vendor=0
                case "$(echo "${VR_HOTSPOT_FORCE_VENDOR_BIN:-}" | tr '[:upper:]' '[:lower:]')" in
                    1|true|yes|on) force_vendor=1 ;;
                esac
                if [ "$force_vendor" -eq 0 ]; then
                    DEPENDENCIES+=("hostapd" "dnsmasq")
                fi
            fi
            ;;
    esac
}

install_dependencies() {
    print_step "Installing dependencies..."
    if [[ "$OS_ID" == "steamos" ]]; then
        local missing=()
        local cmd
        for cmd in python python3 iw ip nmcli firewall-cmd nft iptables git openssl; do
            command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
        done

        if [ "${#missing[@]}" -gt 0 ]; then
            print_error "SteamOS is missing required base tools: ${missing[*]}"
            print_info "Not modifying the immutable SteamOS base automatically. Install the missing tools or use a local repo checkout."
            exit 1
        fi

        print_success "SteamOS base tools found. Using bundled VRhotspot networking stack."
        return 0
    fi

    case "$PKG_MANAGER" in
        pacman)
            local deps=(python python-pip iw iproute2)
            deps+=("dnsmasq")
            if pacman -Qi iptables-nft &>/dev/null || pacman -Si iptables-nft &>/dev/null; then
                deps+=("iptables-nft")
            elif pacman -Qi nftables &>/dev/null || command -v nft &>/dev/null; then
                :
            else
                deps+=("iptables")
            fi
            if [[ ! -r /etc/pacman.d/gnupg/pubring.gpg ]]; then
                print_info "Initializing pacman keyring..."
                install -d -m 755 /etc/pacman.d/gnupg
                pacman-key --init
                local keyrings=()
                if [[ -d /usr/share/pacman/keyrings ]]; then
                    local f base
                    for f in /usr/share/pacman/keyrings/*.gpg; do
                        [[ -e "$f" ]] || continue
                        base=$(basename "$f" .gpg)
                        base=${base%-trusted}
                        base=${base%-revoked}
                        keyrings+=("$base")
                    done
                fi
                if [[ ${#keyrings[@]} -gt 0 ]]; then
                    local uniq=()
                    local k seen
                    for k in "${keyrings[@]}"; do
                        local found=0
                        for seen in "${uniq[@]}"; do
                            if [[ "$seen" == "$k" ]]; then
                                found=1
                                break
                            fi
                        done
                        [[ "$found" -eq 0 ]] && uniq+=("$k")
                    done
                    pacman-key --populate "${uniq[@]}"
                else
                    pacman-key --populate
                fi
            fi
            pacman -Sy --noconfirm --needed "${deps[@]}"
            ;;
        apt)
            _apt_update_with_retry
            apt-get install -y python3 python3-pip python3-venv iw iproute2 iptables hostapd dnsmasq
            ;;
        dnf)
            dnf install -y python3 python3-pip iw iproute iptables
            ;;
        rpm-ostree)
            # Check for missing dependencies to avoid unnecessary layering
            local force_vendor=0
            case "$(echo "${VR_HOTSPOT_FORCE_VENDOR_BIN:-}" | tr '[:upper:]' '[:lower:]')" in
                1|true|yes|on) force_vendor=1 ;;
            esac

            local deps=("python3" "python3-pip" "iw" "iproute" "iptables")
            if [ "$OS_ID" = "bazzite" ]; then
                print_info "Bazzite uses bundled hostapd/dnsmasq; they will not be layered with rpm-ostree."
            elif [ "$force_vendor" -eq 1 ]; then
                print_info "VR_HOTSPOT_FORCE_VENDOR_BIN=1 set; skipping hostapd/dnsmasq layering."
            else
                deps+=("hostapd" "dnsmasq")
            fi
            local needed=()
            for pkg in "${deps[@]}"; do
                if ! rpm -q --whatprovides "$pkg" &>/dev/null; then
                    needed+=("$pkg")
                fi
            done
            
            if [ ${#needed[@]} -gt 0 ]; then
                print_info "Installing missing dependencies: ${needed[*]}"
                if _run_rpm_ostree_install --apply-live "${needed[@]}"; then
                    :
                else
                    local rpm_ostree_status=$?
                    if [ "$rpm_ostree_status" -eq 2 ]; then
                        exit 0
                    fi
                    print_warning "Live install failed. Trying standard install..."
                    if _run_rpm_ostree_install "${needed[@]}"; then
                        :
                    else
                        rpm_ostree_status=$?
                        if [ "$rpm_ostree_status" -eq 2 ]; then
                            exit 0
                        fi
                        return "$rpm_ostree_status"
                    fi
                    print_warning "Dependencies installed. Please REBOOT your system and run this installer again."
                    exit 0
                fi
            fi
            ;;
    esac
    print_success "Dependencies installed."
}

print_dependency_summary() {
    calculate_dependency_list
    print_info "Dependency plan for ${OS_NAME:-$OS_ID}: ${DEPENDENCIES[*]}"
}

get_source_files() {
    print_step "Getting source files..."
    if [ -f "pyproject.toml" ] && [ -d "backend" ]; then
        print_success "Using local files."
        TEMP_INSTALL_DIR=$(pwd)
    else
        if ! command -v git &>/dev/null; then
            print_error "'git' is not installed. Please install it to clone the repository."
            exit 1
        fi
        TEMP_INSTALL_DIR="/tmp/vr-hotspot-install-$$"
        local install_ref
        install_ref="${VR_HOTSPOT_INSTALL_REF:-main}"
        print_info "Cloning repository ref $install_ref to $TEMP_INSTALL_DIR..."
        git clone -q --branch "$install_ref" https://github.com/josethevrtech/VRhotspot.git "$TEMP_INSTALL_DIR"
        print_success "Repository cloned."
    fi
}

configure_install() {
    print_step "Configuring installation..."
    if [ "$INTERACTIVE" -eq 1 ]; then
        if prompt_yes_no "Enable hotspot autostart at boot? (y/N) " "n"; then
            ENABLE_AUTOSTART="y"
        else
            ENABLE_AUTOSTART="n"
        fi

        if prompt_yes_no "Enable remote access? (NOT for public networks) (y/N) " "n"; then
            ENABLE_REMOTE="y"
        else
            ENABLE_REMOTE="n"
        fi
    else
        print_info "Using defaults: autostart disabled, remote access disabled."
        ENABLE_AUTOSTART="n"
        ENABLE_REMOTE="n"
    fi
    configure_flatpak_companion_install

    BIND_IP="127.0.0.1"
    if [ "$ENABLE_REMOTE" == "y" ]; then
        BIND_IP="0.0.0.0"
        print_info "Remote access enabled. Binding to $BIND_IP."
        
        open_remote_access_firewall
    else
        print_info "Remote access disabled. Binding to $BIND_IP."
    fi

    print_info "Generating new API token..."
    API_TOKEN=$(openssl rand -hex 32)
    
    mkdir -p "$CONFIG_DIR"
    {
        echo "VR_HOTSPOTD_API_TOKEN=$API_TOKEN"
        echo "VR_HOTSPOTD_HOST=$BIND_IP"
        echo "VR_HOTSPOTD_PORT=8732"
        # Pop!_OS stability: keep destructive WiFi driver reload recovery disabled by default.
        echo "VR_HOTSPOTD_ENABLE_DRIVER_RELOAD_RECOVERY=0"
    } > "$ENV_FILE"
    if [ "$OS_ID" = "steamos" ]; then
        echo "VR_HOTSPOT_VENDOR_PROFILE=steamos" >> "$ENV_FILE"
        echo "VR_HOTSPOT_FORCE_VENDOR_BIN=1" >> "$ENV_FILE"
        echo "VR_HOTSPOT_VENDOR_STRICT=1" >> "$ENV_FILE"
        print_info "SteamOS detected; forcing bundled networking stack."
    fi
    if [ "$OS_ID" = "bazzite" ]; then
        echo "VR_HOTSPOT_VENDOR_PROFILE=bazzite" >> "$ENV_FILE"
        echo "VR_HOTSPOT_FORCE_VENDOR_BIN=1" >> "$ENV_FILE"
        print_info "Bazzite detected; forcing bundled hostapd/dnsmasq."
    fi
    print_success "Configuration saved to $ENV_FILE."
}

install_daemon() {
    print_step "Installing daemon using backend script..."
    
    local backend_install_script="$TEMP_INSTALL_DIR/backend/scripts/install.sh"
    if [ ! -f "$backend_install_script" ]; then
        print_error "Backend install script not found at $backend_install_script"
        exit 1
    fi

    local install_args=()
    if [ "$ENABLE_AUTOSTART" == "y" ]; then
        install_args+=("--enable-autostart")
    else
        install_args+=("--disable-autostart")
    fi

    bash "$backend_install_script" "${install_args[@]}"
    
    print_success "Daemon installation complete."
}

validate_endeavouros_runtime_dependencies() {
    if [[ "$OS_ID" != "endeavouros" ]]; then
        return 0
    fi

    local vendor_bin_dir="$TEMP_INSTALL_DIR/backend/vendor/bin"
    local -a missing=()

    if ! command -v hostapd >/dev/null 2>&1 && [ ! -x "$vendor_bin_dir/hostapd" ]; then
        missing+=("hostapd (system or bundled)")
    fi
    if ! command -v dnsmasq >/dev/null 2>&1; then
        missing+=("dnsmasq")
    fi

    if [ "${#missing[@]}" -gt 0 ]; then
        print_error "EndeavourOS is missing required runtime dependencies: ${missing[*]}"
        return 1
    fi

    print_success "EndeavourOS runtime dependencies found (hostapd and dnsmasq)."
}

enable_firewalld_uplink_forwarding() {
    if ! command -v firewall-cmd >/dev/null 2>&1; then
        print_info "firewalld not installed; skipping uplink forwarding setup"
        return 0
    fi
    if ! firewall-cmd --state >/dev/null 2>&1; then
        print_info "firewalld not running; skipping uplink forwarding setup"
        return 0
    fi

    local uplink
    uplink="$(ip route show default 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
    if [[ -z "$uplink" ]]; then
        print_info "No default route interface detected; skipping uplink forwarding setup"
        return 0
    fi

    local zone
    zone="$(firewall-cmd --get-zone-of-interface="$uplink" 2>/dev/null | head -n1 | tr -d '\r')"
    if [[ -z "$zone" ]]; then
        zone="$(firewall-cmd --get-default-zone 2>/dev/null | head -n1 | tr -d '\r')"
    fi
    if [[ -z "$zone" ]]; then
        print_warning "Unable to determine firewalld zone for uplink $uplink; skipping"
        return 0
    fi

    print_info "Ensuring firewalld forwarding for uplink $uplink (zone=$zone)"
    ensure_firewalld_action "add-masquerade" "runtime" "$zone"
    ensure_firewalld_action "add-forward" "runtime" "$zone"
    ensure_firewalld_action "add-masquerade" "permanent" "$zone"
    ensure_firewalld_action "add-forward" "permanent" "$zone"
}

show_completion() {
    local primary_ip
    primary_ip=$(ip route get 1.1.1.1 | awk -F"src " '{print $2}' | awk '{print $1}')
    
    clear
    echo -e "${GREEN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                  🎉 INSTALLATION COMPLETE! 🎉                   ║"
    echo "╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo
    print_success "$APP_NAME is installed and running!"
    echo
    echo -e "${CYAN}📱 ${BOLD}Access the Web UI:${NC}"
    if [ "$ENABLE_REMOTE" == "y" ]; then
        echo -e "   - On this device:  ${BOLD}http://localhost:8732${NC}"
        [ -n "$primary_ip" ] && echo -e "   - On other devices: ${BOLD}http://$primary_ip:8732${NC}"
    else
        echo -e "   - On this device:  ${BOLD}http://localhost:8732${NC}"
    fi
    echo
    echo -e "${CYAN}🔑 ${BOLD}Your API Token:${NC}"
    echo -e "   Copy and paste this token into the Web UI to authenticate:"
    echo -e "   ${YELLOW}${BOLD}$API_TOKEN${NC}"
    echo
    echo -e "${CYAN}💡 ${BOLD}Next Steps:${NC}"
    echo -e "   1. Open the URL above in a browser."
    echo -e "   2. Paste the token when prompted."
    echo -e "   3. Configure and start your hotspot!"
    echo
    echo -e "${CYAN}🔧 ${BOLD}Useful Commands:${NC}"
    echo -e "   - Status:      ${BOLD}sudo systemctl status $DAEMON_UNIT${NC}"
    echo -e "   - Logs:        ${BOLD}sudo journalctl -u $DAEMON_UNIT -f${NC}"
    echo -e "   - Preflight:   ${BOLD}sudo $INSTALL_ROOT/bin/vr-hotspot preflight${NC}"
    echo -e "   - Uninstall:   ${BOLD}sudo bash $APP_DIR/uninstall.sh${NC}"
    echo
}

# --- Main Execution ---
main() {
    CHECK_ONLY=0
    SKIP_CLEAR=0
    REQUESTED_INTERACTIVE_MODE="auto"
    FLATPAK_COMPANION_OPT_IN=0
    INSTALL_FLATPAK_COMPANION="n"

    for arg in "$@"; do
        case "$arg" in
            --interactive)
                REQUESTED_INTERACTIVE_MODE="interactive"
                ;;
            --non-interactive|--yes|-y)
                REQUESTED_INTERACTIVE_MODE="non-interactive"
                ;;
            --install-flatpak-companion)
                FLATPAK_COMPANION_OPT_IN=1
                ;;
            --check-os)
                CHECK_ONLY=1
                ;;
            --no-clear)
                SKIP_CLEAR=1
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                ;;
        esac
    done

    resolve_interactive_mode "$REQUESTED_INTERACTIVE_MODE"
    
    if [ "$SKIP_CLEAR" -eq 0 ] && [ -t 1 ]; then
        clear
    fi
    print_header

    if [ "$INTERACTIVE" -eq 0 ] && [ -n "$NON_INTERACTIVE_REASON" ]; then
        print_info "Non-interactive mode selected ($NON_INTERACTIVE_REASON); prompts are disabled and defaults will be used."
    fi

    if [ "$CHECK_ONLY" -eq 1 ]; then
        detect_os
        print_dependency_summary
        return 0
    fi
    
    check_root
    cleanup_previous_install
    
    if [ "$INTERACTIVE" -eq 1 ]; then
        if ! prompt_yes_no "Continue with installation? (Y/n) " "y"; then
            print_info "Installation cancelled."
            exit 0
        fi
    fi
    
    detect_os
    install_dependencies
    get_source_files
    validate_endeavouros_runtime_dependencies
    configure_install
    install_daemon
    if [[ "$OS_ID" == "steamos" || "$OS_ID" == "bazzite" || "$OS_ID" == "fedora" || "$OS_ID" == "endeavouros" || "$OS_ID_LIKE" == *"fedora"* ]]; then
        enable_firewalld_uplink_forwarding
    fi
    install_flatpak_companion_if_requested
    
    show_completion

    # Clean up cloned repo if necessary
    if [[ "$TEMP_INSTALL_DIR" == /tmp/* ]]; then
        rm -rf "$TEMP_INSTALL_DIR"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
