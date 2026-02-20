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
CONFIG_DIR="/etc/vr-hotspot"
ENV_FILE="$CONFIG_DIR/env"
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

    if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
        print_info "Removing firewall rules..."
        firewall-cmd --permanent --remove-port=8732/tcp &>/dev/null || true
        firewall-cmd --reload &>/dev/null || true
    fi

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
        steamos|cachyos|arch) PKG_MANAGER="pacman" ;;
        ubuntu|debian|pop) PKG_MANAGER="apt" ;;
        fedora) PKG_MANAGER="dnf" ;;
        bazzite) PKG_MANAGER="rpm-ostree" ;;
        *)
            print_error "Unsupported OS: $OS_ID. Please install dependencies manually."
            exit 1
            ;;
    esac
    print_success "Detected $OS_NAME ($PKG_MANAGER)."
}

calculate_dependency_list() {
    DEPENDENCIES=()
    case "$PKG_MANAGER" in
        pacman)
            DEPENDENCIES=(python python-pip iw iproute2)
            if [[ "$OS_ID" != "steamos" ]]; then
                DEPENDENCIES+=("iptables")
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
            local script_dir vendor_bundle force_vendor
            script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
            vendor_bundle=0
            if [ "$OS_ID" = "bazzite" ] && [ -x "$script_dir/backend/vendor/bin/bazzite/hostapd" ]; then
                vendor_bundle=1
            fi
            force_vendor=0
            case "$(echo "${VR_HOTSPOT_FORCE_VENDOR_BIN:-}" | tr '[:upper:]' '[:lower:]')" in
                1|true|yes|on) force_vendor=1 ;;
            esac
            if [ "$vendor_bundle" -eq 0 ] && [ "$force_vendor" -eq 0 ]; then
                DEPENDENCIES+=("hostapd" "dnsmasq")
            fi
            ;;
    esac
}

install_dependencies() {
    print_step "Installing dependencies..."
    case "$PKG_MANAGER" in
        pacman)
            local deps=(python python-pip iw iproute2)
            if [[ "$OS_ID" == "steamos" ]]; then
                : # SteamOS ships iptables-nft by default; don't install iptables packages.
            else
                if pacman -Qi iptables-nft &>/dev/null || pacman -Si iptables-nft &>/dev/null; then
                    deps+=("iptables-nft")
                elif pacman -Qi nftables &>/dev/null || command -v nft &>/dev/null; then
                    :
                else
                    deps+=("iptables")
                fi
            fi
            [[ "$OS_ID" == "steamos" ]] && steamos-readonly disable || true
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
            [[ "$OS_ID" == "steamos" ]] && steamos-readonly enable || true
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
            local script_dir
            script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
            local vendor_bundle=0
            if [ "$OS_ID" = "bazzite" ] && [ -x "$script_dir/backend/vendor/bin/bazzite/hostapd" ]; then
                vendor_bundle=1
            fi

            local force_vendor=0
            case "$(echo "${VR_HOTSPOT_FORCE_VENDOR_BIN:-}" | tr '[:upper:]' '[:lower:]')" in
                1|true|yes|on) force_vendor=1 ;;
            esac

            local deps=("python3" "python3-pip" "iw" "iproute" "iptables")
            if [ "$vendor_bundle" -eq 1 ]; then
                print_info "Bazzite vendor bundle detected; skipping hostapd/dnsmasq layering."
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
                if ! rpm-ostree install --apply-live "${needed[@]}"; then
                    print_warning "Live install failed. Trying standard install..."
                    rpm-ostree install "${needed[@]}"
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
        print_info "Cloning repository to $TEMP_INSTALL_DIR..."
        git clone -q https://github.com/josethevrtech/VRhotspot.git "$TEMP_INSTALL_DIR"
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

    BIND_IP="127.0.0.1"
    if [ "$ENABLE_REMOTE" == "y" ]; then
        BIND_IP="0.0.0.0"
        print_info "Remote access enabled. Binding to $BIND_IP."
        
        if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
            print_info "Opening port 8732/tcp in firewall..."
            # Open in default zone
            firewall-cmd --permanent --add-port=8732/tcp &>/dev/null || true
            # Explicitly try public and FedoraWorkstation zones which are common on Fedora
            firewall-cmd --permanent --zone=public --add-port=8732/tcp &>/dev/null || true
            firewall-cmd --permanent --zone=FedoraWorkstation --add-port=8732/tcp &>/dev/null || true
            
            firewall-cmd --reload &>/dev/null || print_warning "Failed to reload firewall."
        elif command -v ufw &>/dev/null; then
             print_info "Opening port 8732/tcp in UFW..."
             ufw allow 8732/tcp &>/dev/null || print_warning "Failed to add UFW rule."
        else
            print_warning "No supported firewall manager found (firewalld/ufw). Please manually open TCP port 8732."
        fi
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
    if [ "$OS_ID" = "bazzite" ]; then
        echo "VR_HOTSPOT_VENDOR_PROFILE=bazzite" >> "$ENV_FILE"
        if [ -x "$TEMP_INSTALL_DIR/backend/vendor/bin/bazzite/hostapd" ]; then
            echo "VR_HOTSPOT_FORCE_VENDOR_BIN=1" >> "$ENV_FILE"
            print_info "Bazzite vendor bundle detected; forcing bundled hostapd/dnsmasq."
        else
            print_warning "Bazzite vendor bundle not found. Using system hostapd until bundled binaries are added."
        fi
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
    firewall-cmd --zone "$zone" --add-masquerade &>/dev/null || \
        print_warning "Failed to enable masquerade for zone $zone (runtime)"
    firewall-cmd --zone "$zone" --add-forward &>/dev/null || \
        print_warning "Failed to enable forward for zone $zone (runtime)"
    firewall-cmd --permanent --zone "$zone" --add-masquerade &>/dev/null || \
        print_warning "Failed to enable masquerade for zone $zone (permanent)"
    firewall-cmd --permanent --zone "$zone" --add-forward &>/dev/null || \
        print_warning "Failed to enable forward for zone $zone (permanent)"
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
    echo -e "   - Uninstall:   ${BOLD}sudo bash $APP_DIR/uninstall.sh${NC}"
    echo
}

# --- Main Execution ---
main() {
    CHECK_ONLY=0
    SKIP_CLEAR=0
    for arg in "$@"; do
        case "$arg" in
            --check-os)
                CHECK_ONLY=1
                ;;
            --no-clear)
                SKIP_CLEAR=1
                ;;
            *)
                ;;
        esac
    done

    [ -t 1 ] && INTERACTIVE=1 || INTERACTIVE=0
    
    if [ "$SKIP_CLEAR" -eq 0 ]; then
        clear
    fi
    print_header

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
    configure_install
    install_daemon
    if [[ "$OS_ID" == "bazzite" || "$OS_ID" == "fedora" || "$OS_ID_LIKE" == *"fedora"* ]]; then
        enable_firewalld_uplink_forwarding
    fi
    
    show_completion

    # Clean up cloned repo if necessary
    if [[ "$TEMP_INSTALL_DIR" == /tmp/* ]]; then
        rm -rf "$TEMP_INSTALL_DIR"
    fi
}

main "$@"

