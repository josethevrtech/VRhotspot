#!/usr/bin/env bash
# VR Hotspot - Interactive Installer
# One-command installation for SteamOS and CachyOS
# Usage: curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh | sudo bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Detect if running in a terminal
if [ -t 1 ]; then
    INTERACTIVE=1
else
    INTERACTIVE=0
fi

print_header() {
    echo -e "${CYAN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║              VR Hotspot - Interactive Installer                 ║"
    echo "║              VR-grade Wi-Fi Hotspot for SteamOS/Linux           ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_step() {
    echo -e "${BLUE}${BOLD}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${CYAN}ℹ $1${NC}"
}

# Detect OS
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="$ID"
        OS_NAME="$NAME"
        OS_VERSION="$VERSION_ID"
    else
        print_error "Cannot detect OS. /etc/os-release not found."
        exit 1
    fi

    # Detect specific distros
    if [[ "$OS_ID" == "steamos" ]] || [[ "$OS_NAME" == *"SteamOS"* ]]; then
        DISTRO="steamos"
        PKG_MANAGER="pacman"
    elif [[ "$OS_ID" == "cachyos" ]] || [[ "$OS_NAME" == *"CachyOS"* ]]; then
        DISTRO="cachyos"
        PKG_MANAGER="pacman"
    elif [[ "$OS_ID" == "arch" ]] || [[ "$OS_NAME" == *"Arch"* ]]; then
        DISTRO="arch"
        PKG_MANAGER="pacman"
    elif [[ "$OS_ID" == "ubuntu" ]] || [[ "$OS_ID" == "debian" ]]; then
        DISTRO="debian"
        PKG_MANAGER="apt"
    elif [[ "$OS_ID" == "fedora" ]] || [[ "$OS_ID" == "rhel" ]] || [[ "$OS_ID" == "centos" ]]; then
        DISTRO="fedora"
        PKG_MANAGER="dnf"
    else
        DISTRO="unknown"
        PKG_MANAGER="unknown"
    fi
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This installer requires root privileges."
        print_info "Please run with: sudo bash install.sh"
        exit 1
    fi
}

check_internet() {
    print_step "Checking internet connection..."
    if ping -c 1 -W 2 8.8.8.8 &> /dev/null; then
        print_success "Internet connection OK"
        return 0
    else
        print_warning "No internet connection detected"
        print_info "This installer can work offline if you have the repository cloned locally."
        return 1
    fi
}

install_dependencies() {
    print_step "Installing system dependencies..."
    
    case "$PKG_MANAGER" in
        pacman)
            # Check if SteamOS read-only
            if [[ "$DISTRO" == "steamos" ]]; then
                print_info "Disabling SteamOS read-only filesystem..."
                steamos-readonly disable || true
            fi
            
            print_info "Updating package database..."
            pacman -Sy --noconfirm
            
            print_info "Installing required packages..."
            pacman -S --needed --noconfirm \
                python \
                python-pip \
                iw \
                iproute2 \
                iptables \
                rfkill \
                wireless_tools \
                net-tools || true
            
            # libnl is bundled, but offer to install system version
            if ! pacman -Q libnl &>/dev/null; then
                print_info "Installing libnl (backup, we bundle it)..."
                pacman -S --needed --noconfirm libnl || true
            fi
            
            if [[ "$DISTRO" == "steamos" ]]; then
                print_info "Re-enabling SteamOS read-only filesystem..."
                steamos-readonly enable || true
            fi
            ;;
            
        apt)
            print_info "Updating package database..."
            apt-get update -qq
            
            print_info "Installing required packages..."
            apt-get install -y \
                python3 \
                python3-pip \
                iw \
                iproute2 \
                iptables \
                rfkill \
                wireless-tools \
                net-tools \
                libnl-3-200 \
                libnl-genl-3-200 || true
            ;;
            
        dnf)
            print_info "Installing required packages..."
            dnf install -y \
                python3 \
                python3-pip \
                iw \
                iproute \
                iptables \
                rfkill \
                wireless-tools \
                net-tools \
                libnl3 || true
            ;;
            
        *)
            print_error "Unsupported package manager: $PKG_MANAGER"
            print_info "Please install manually: python3, iw, iproute2, iptables, rfkill"
            exit 1
            ;;
    esac
    
    print_success "Dependencies installed"
}

download_or_use_local() {
    print_step "Setting up VR Hotspot..."
    
    # Check if we're already in the repo directory
    if [ -f "backend/vr_hotspotd/main.py" ] && [ -f "pyproject.toml" ]; then
        print_info "Using local repository"
        INSTALL_DIR="$(pwd)"
        return 0
    fi
    
    # Try to download from GitHub
    if command -v git &> /dev/null && check_internet; then
        print_info "Cloning repository from GitHub..."
        INSTALL_DIR="/tmp/vr-hotspot-install-$$"
        git clone https://github.com/josethevrtech/VRhotspot.git "$INSTALL_DIR" || {
            print_error "Failed to clone repository"
            exit 1
        }
    else
        print_error "Cannot find VR Hotspot files"
        print_info "Please either:"
        print_info "  1. Run this script from the VR Hotspot directory"
        print_info "  2. Install git and ensure internet connection"
        exit 1
    fi
}

configure_networkmanager() {
    print_step "Configuring NetworkManager..."
    
    if ! command -v nmcli &> /dev/null; then
        print_info "NetworkManager not found, skipping"
        return 0
    fi
    
    mkdir -p /etc/NetworkManager/conf.d/
    cat > /etc/NetworkManager/conf.d/vr-hotspot.conf << 'EOF'
[keyfile]
# Prevent NetworkManager from managing WiFi adapters used by VR Hotspot
# This prevents interference with AP mode
unmanaged-devices=interface-name:wlan0;interface-name:wlan1;interface-name:wlan2
EOF
    
    systemctl restart NetworkManager 2>/dev/null || true
    print_success "NetworkManager configured"
}

install_daemon() {
    print_step "Installing VR Hotspot daemon..."
    
    cd "$INSTALL_DIR"
    
    # Run the backend install script
    if [ -f "backend/scripts/install.sh" ]; then
        bash backend/scripts/install.sh
        print_success "Daemon installed"
    else
        print_error "Install script not found"
        exit 1
    fi
}

enable_and_start_service() {
    print_step "Enabling and starting service..."
    
    systemctl daemon-reload
    systemctl enable vr-hotspotd.service
    systemctl start vr-hotspotd.service
    
    sleep 2
    
    if systemctl is-active --quiet vr-hotspotd; then
        print_success "Service started successfully"
    else
        print_warning "Service may not have started correctly"
        print_info "Check status with: sudo systemctl status vr-hotspotd"
    fi
}

check_adapters() {
    print_step "Checking WiFi adapters..."
    
    if command -v iw &> /dev/null; then
        ADAPTERS=$(iw dev 2>/dev/null | grep Interface | awk '{print $2}')
        if [ -n "$ADAPTERS" ]; then
            print_success "Found WiFi adapters:"
            echo "$ADAPTERS" | while read -r adapter; do
                echo "  • $adapter"
            done
        else
            print_warning "No WiFi adapters found"
            print_info "Make sure your WiFi adapter supports AP mode"
        fi
    else
        print_warning "Cannot check adapters (iw command not available yet)"
    fi
}

get_api_token() {
    if [ -f "/var/lib/vr-hotspot/api_token" ]; then
        TOKEN=$(cat /var/lib/vr-hotspot/api_token)
        echo "$TOKEN"
    else
        echo ""
    fi
}

show_completion() {
    clear
    print_header
    
    echo -e "${GREEN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                  🎉 INSTALLATION COMPLETE! 🎉                   ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
    
    print_success "VR Hotspot is installed and running!"
    echo ""
    
    echo -e "${CYAN}${BOLD}📱 Access the Web UI:${NC}"
    echo -e "   ${BOLD}http://localhost:8732${NC}"
    echo ""
    
    TOKEN=$(get_api_token)
    if [ -n "$TOKEN" ]; then
        echo -e "${CYAN}${BOLD}🔑 Your API Token:${NC}"
        echo -e "   ${BOLD}$TOKEN${NC}"
        echo -e "   ${YELLOW}(Save this! You'll need it to access the UI)${NC}"
        echo ""
    fi
    
    echo -e "${CYAN}${BOLD}🎮 Quick Start:${NC}"
    echo "   1. Open the web UI in your browser"
    echo "   2. Enter the API token if prompted"
    echo "   3. Click 'Start' to create your hotspot"
    echo "   4. Connect your VR headset to the new network"
    echo ""
    
    echo -e "${CYAN}${BOLD}📊 Useful Commands:${NC}"
    echo "   • Check status:  ${BOLD}sudo systemctl status vr-hotspotd${NC}"
    echo "   • View logs:     ${BOLD}sudo journalctl -u vr-hotspotd -f${NC}"
    echo "   • Restart:       ${BOLD}sudo systemctl restart vr-hotspotd${NC}"
    echo "   • Stop:          ${BOLD}sudo systemctl stop vr-hotspotd${NC}"
    echo "   • Uninstall:     ${BOLD}sudo bash /var/lib/vr-hotspot/app/backend/scripts/uninstall.sh${NC}"
    echo ""
    
    echo -e "${CYAN}${BOLD}💡 Tips:${NC}"
    echo "   • Use wlan1 if available (better AP mode support)"
    echo "   • For VR streaming, use 5GHz band for best performance"
    echo "   • Enable QoS for stable low-latency connection"
    echo ""
    
    echo -e "${CYAN}${BOLD}📚 Documentation:${NC}"
    echo "   • README: /var/lib/vr-hotspot/app/README.md"
    echo "   • Troubleshooting: Check web UI diagnostics"
    echo ""
    
    if [[ "$DISTRO" == "steamos" ]]; then
        echo -e "${YELLOW}${BOLD}⚠️  SteamOS Note:${NC}"
        echo "   Updates may reset the read-only filesystem."
        echo "   If the service stops working after an update, reinstall with:"
        echo "   ${BOLD}curl -sSL YOUR_INSTALL_URL | sudo bash${NC}"
        echo ""
    fi
    
    echo -e "${GREEN}Enjoy your VR-grade WiFi hotspot! 🚀${NC}"
    echo ""
}

# Main installation flow
main() {
    clear
    print_header
    
    echo -e "${BOLD}This will install VR Hotspot on your system.${NC}"
    echo ""
    
    # Pre-flight checks
    check_root
    detect_os
    
    echo -e "${CYAN}Detected OS:${NC} $OS_NAME ($DISTRO)"
    echo -e "${CYAN}Package Manager:${NC} $PKG_MANAGER"
    echo ""
    
    if [ "$INTERACTIVE" -eq 1 ]; then
        read -p "Continue with installation? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Installation cancelled"
            exit 0
        fi
        echo ""
    fi
    
    # Installation steps
    check_internet || true
    install_dependencies
    download_or_use_local
    configure_networkmanager
    install_daemon
    check_adapters
    enable_and_start_service
    
    # Cleanup
    if [[ "$INSTALL_DIR" == /tmp/* ]]; then
        rm -rf "$INSTALL_DIR"
    fi
    
    # Show completion
    show_completion
}

# Run main installation
main "$@"
