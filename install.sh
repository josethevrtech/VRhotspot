#!/usr/bin/env bash
# VR Hotspot - Enhanced Interactive Installer

set -e

# --- Configuration ---
APP_NAME="VR Hotspot"
SERVICE_NAME="vr-hotspotd"
INSTALL_ROOT="/var/lib/vr-hotspot"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
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
    echo "╚══════════════════════════════════════════════════════════════════╝${NC}"
}

print_step() { echo -e "${BLUE}${BOLD}▶ $1${NC}"; }
print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_error() { echo -e "${RED}✗ $1${NC}"; }
print_info() { echo -e "${CYAN}ℹ $1${NC}"; }

# --- Pre-flight & Cleanup ---
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This installer requires root privileges. Please run with 'sudo'."
        exit 1
    fi
}

cleanup_previous_install() {
    print_step "Checking for existing installation..."
    if [ ! -d "$INSTALL_ROOT" ] && ! systemctl list-unit-files | grep -q "$SERVICE_NAME.service"; then
        print_success "No existing installation found."
        return
    fi

    print_warning "Existing $APP_NAME installation detected."
    if [ "$INTERACTIVE" -eq 1 ]; then
        read -p "Perform a full cleanup of the previous version? (Y/n) " -n 1 -r REPLY
        echo
        if [[ "$REPLY" =~ ^[Nn]$ ]]; then
            print_error "Cannot proceed with an existing installation. Aborting."
            exit 1
        fi
    fi

    print_info "Cleaning up previous installation..."
    systemctl stop "$SERVICE_NAME.service" &>/dev/null || true
    systemctl disable "$SERVICE_NAME.service" &>/dev/null || true
    pkill -f "vr_hotspotd/main.py" &>/dev/null || true

    if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
        print_info "Removing firewall rules..."
        firewall-cmd --permanent --remove-port=8732/tcp &>/dev/null || true
        firewall-cmd --reload &>/dev/null || true
    fi

    rm -f "$SYSTEMD_DIR/$SERVICE_NAME.service"
    rm -f "$SYSTEMD_DIR/$SERVICE_NAME-autostart.service"
    systemctl daemon-reload

    print_info "Removing files and directories..."
    rm -rf "$INSTALL_ROOT" "$CONFIG_DIR" "/run/vr-hotspot" "/tmp/vr-hotspot-*"
    print_success "Cleanup complete."
}

# --- Installation Steps ---
detect_os() {
    print_step "Detecting Operating System..."
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="$ID"
    else
        print_error "Cannot detect OS (/etc/os-release not found)."
        exit 1
    fi
    case "$OS_ID" in
        steamos|cachyos|arch) PKG_MANAGER="pacman" ;;
        ubuntu|debian) PKG_MANAGER="apt" ;;
        fedora) PKG_MANAGER="dnf" ;;
        bazzite) PKG_MANAGER="rpm-ostree" ;;
        *)
            print_error "Unsupported OS: $OS_ID. Please install dependencies manually."
            exit 1
            ;;
    esac
    print_success "Detected $NAME ($PKG_MANAGER)."
}

install_dependencies() {
    print_step "Installing dependencies..."
    case "$PKG_MANAGER" in
        pacman)
            [[ "$OS_ID" == "steamos" ]] && steamos-readonly disable || true
            pacman -Sy --noconfirm --needed python python-pip iw iproute2 iptables
            [[ "$OS_ID" == "steamos" ]] && steamos-readonly enable || true
            ;;
        apt)
            apt-get update -qq
            apt-get install -y python3 python3-pip python3-venv iw iproute2 iptables
            ;;
        dnf)
            dnf install -y python3 python3-pip python3-venv iw iproute2 iptables
            ;;
        rpm-ostree)
            # Check for missing dependencies to avoid unnecessary layering
            local deps=("python3" "python3-pip" "iw" "iproute" "iptables")
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
        read -p "Enable autostart at boot? (y/N) " -n 1 -r; echo
        [[ "$REPLY" =~ ^[Yy]$ ]] && ENABLE_AUTOSTART="y" || ENABLE_AUTOSTART="n"

        read -p "Enable remote access? (NOT for public networks) (y/N) " -n 1 -r; echo
        [[ "$REPLY" =~ ^[Yy]$ ]] && ENABLE_REMOTE="y" || ENABLE_REMOTE="n"
    else
        print_info "Using defaults: autostart disabled, remote access disabled."
        ENABLE_AUTOSTART="n"
        ENABLE_REMOTE="n"
    fi

    BIND_IP="127.0.0.1"
    if [ "$ENABLE_REMOTE" == "y" ]; then
        BIND_IP="0.0.0.0"
        print_info "Remote access enabled. Binding to $BIND_IP."
    else
        print_info "Remote access disabled. Binding to $BIND_IP."
    fi

    print_info "Generating new API token..."
    API_TOKEN=$(openssl rand -hex 32)
    
    mkdir -p "$CONFIG_DIR"
    {
        echo "VR_HOTSPOTD_API_TOKEN=$API_TOKEN"
        echo "VR_HOTSPOTD_BIND_IP=$BIND_IP"
        echo "VR_HOTSPOTD_PORT=8732"
    } > "$ENV_FILE"
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
    fi

    bash "$backend_install_script" "${install_args[@]}"
    
    print_success "Daemon installation complete."
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
    echo -e "   - Status:      ${BOLD}sudo systemctl status $SERVICE_NAME${NC}"
    echo -e "   - Logs:        ${BOLD}sudo journalctl -u $SERVICE_NAME -f${NC}"
    echo -e "   - Uninstall:   ${BOLD}sudo bash $APP_DIR/uninstall.sh${NC}"
    echo
}

# --- Main Execution ---
main() {
    [ -t 1 ] && INTERACTIVE=1 || INTERACTIVE=0
    
    clear
    print_header
    
    check_root
    cleanup_previous_install
    
    if [ "$INTERACTIVE" -eq 1 ]; then
        read -p "Continue with installation? (Y/n) " -n 1 -r; echo
        if [[ "$REPLY" =~ ^[Nn]$ ]]; then
            print_info "Installation cancelled."
            exit 0
        fi
    fi
    
    detect_os
    install_dependencies
    get_source_files
    configure_install
    install_daemon
    
    show_completion

    # Clean up cloned repo if necessary
    if [[ "$TEMP_INSTALL_DIR" == /tmp/* ]]; then
        rm -rf "$TEMP_INSTALL_DIR"
    fi
}

main "$@"