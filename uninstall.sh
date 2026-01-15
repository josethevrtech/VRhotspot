#!/usr/bin/env bash
# VR Hotspot - Uninstaller

set -e

# --- Configuration ---
APP_NAME="VR Hotspot"
SERVICE_NAME="vr-hotspotd"
INSTALL_ROOT="/var/lib/vr-hotspot"
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

# --- Main Uninstallation Logic ---
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This uninstaller requires root privileges. Please run with 'sudo'."
        exit 1
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
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
    systemctl stop "$SERVICE_NAME.service" &>/dev/null || true
    systemctl disable "$SERVICE_NAME.service" &>/dev/null || true
    systemctl stop "$SERVICE_NAME-autostart.service" &>/dev/null || true
    systemctl disable "$SERVICE_NAME-autostart.service" &>/dev/null || true
    print_success "Services stopped and disabled."

    if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
        print_step "Removing firewall rules..."
        firewall-cmd --permanent --remove-port=8732/tcp &>/dev/null || true
        firewall-cmd --reload &>/dev/null || true
        print_success "Firewall rule removed."
    fi

    print_step "Removing systemd service files..."
    rm -f "$SYSTEMD_DIR/$SERVICE_NAME.service"
    rm -f "$SYSTEMD_DIR/$SERVICE_NAME-autostart.service"
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

main "$@"