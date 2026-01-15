#!/usr/bin/env bash
# VR Hotspot - Interactive Uninstaller
# One-command uninstallation for SteamOS and CachyOS
# Usage: curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/uninstall.sh | sudo bash

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
    echo -e "${RED}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║             VR Hotspot - Interactive Uninstaller                ║"
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

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This uninstaller requires root privileges."
        print_info "Please run with: sudo bash uninstall.sh"
        exit 1
    fi
}

check_installation() {
    print_step "Checking VR Hotspot installation..."
    
    if [ ! -d "/var/lib/vr-hotspot" ]; then
        print_warning "VR Hotspot does not appear to be installed"
        print_info "Installation directory not found: /var/lib/vr-hotspot"
        return 1
    fi
    
    if ! systemctl list-unit-files | grep -q "vr-hotspotd.service"; then
        print_warning "VR Hotspot service not found"
        return 1
    fi
    
    print_success "VR Hotspot installation found"
    return 0
}

show_what_will_be_removed() {
    echo ""
    echo -e "${YELLOW}${BOLD}The following will be removed:${NC}"
    echo ""
    echo "  📁 Files and Directories:"
    echo "     • /var/lib/vr-hotspot/ (installation directory)"
    echo "     • /etc/systemd/system/vr-hotspotd.service"
    echo "     • /etc/systemd/system/vr-hotspot-autostart.service (if exists)"
    echo "     • /var/lib/vr-hotspot/config.json (your configuration)"
    echo "     • /var/lib/vr-hotspot/api_token (your API token)"
    echo ""
    echo "  ⚙️  Services:"
    echo "     • vr-hotspotd.service (stopped and disabled)"
    echo "     • vr-hotspot-autostart.service (if exists)"
    echo ""
    echo "  🔧 System Configuration:"
    echo "     • /etc/NetworkManager/conf.d/vr-hotspot.conf (kept by default)"
    echo ""
    
    # Check if hotspot is running
    if systemctl is-active --quiet vr-hotspotd; then
        echo -e "  ${RED}⚠️  VR Hotspot is currently RUNNING${NC}"
        echo "     It will be stopped during uninstallation"
        echo ""
    fi
}

stop_services() {
    print_step "Stopping VR Hotspot services..."
    
    if systemctl is-active --quiet vr-hotspotd; then
        systemctl stop vr-hotspotd || true
        print_success "Stopped vr-hotspotd service"
    fi
    
    if systemctl is-active --quiet vr-hotspot-autostart 2>/dev/null; then
        systemctl stop vr-hotspot-autostart || true
        print_success "Stopped vr-hotspot-autostart service"
    fi
}

disable_services() {
    print_step "Disabling services..."
    
    if systemctl is-enabled --quiet vr-hotspotd 2>/dev/null; then
        systemctl disable vr-hotspotd || true
        print_success "Disabled vr-hotspotd service"
    fi
    
    if systemctl is-enabled --quiet vr-hotspot-autostart 2>/dev/null; then
        systemctl disable vr-hotspot-autostart || true
        print_success "Disabled vr-hotspot-autostart service"
    fi
}

remove_service_files() {
    print_step "Removing service files..."
    
    if [ -f "/etc/systemd/system/vr-hotspotd.service" ]; then
        rm -f /etc/systemd/system/vr-hotspotd.service
        print_success "Removed vr-hotspotd.service"
    fi
    
    if [ -f "/etc/systemd/system/vr-hotspot-autostart.service" ]; then
        rm -f /etc/systemd/system/vr-hotspot-autostart.service
        print_success "Removed vr-hotspot-autostart.service"
    fi
    
    systemctl daemon-reload
}

backup_config() {
    print_step "Backing up configuration..."
    
    BACKUP_DIR="$HOME/vr-hotspot-backup-$(date +%Y%m%d-%H%M%S)"
    
    if [ -f "/var/lib/vr-hotspot/config.json" ] || [ -f "/var/lib/vr-hotspot/api_token" ]; then
        mkdir -p "$BACKUP_DIR"
        
        if [ -f "/var/lib/vr-hotspot/config.json" ]; then
            cp /var/lib/vr-hotspot/config.json "$BACKUP_DIR/" || true
        fi
        
        if [ -f "/var/lib/vr-hotspot/api_token" ]; then
            cp /var/lib/vr-hotspot/api_token "$BACKUP_DIR/" || true
        fi
        
        print_success "Configuration backed up to: $BACKUP_DIR"
        echo "  (You can delete this if you don't need it)"
    else
        print_info "No configuration to back up"
    fi
}

remove_installation_directory() {
    print_step "Removing installation directory..."
    
    if [ -d "/var/lib/vr-hotspot" ]; then
        rm -rf /var/lib/vr-hotspot
        print_success "Removed /var/lib/vr-hotspot"
    fi
}

cleanup_networkmanager() {
    if [ -f "/etc/NetworkManager/conf.d/vr-hotspot.conf" ]; then
        echo ""
        print_info "NetworkManager configuration found: /etc/NetworkManager/conf.d/vr-hotspot.conf"
        
        if [ "$INTERACTIVE" -eq 1 ]; then
            read -p "Remove NetworkManager configuration? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                rm -f /etc/NetworkManager/conf.d/vr-hotspot.conf
                systemctl restart NetworkManager 2>/dev/null || true
                print_success "NetworkManager configuration removed"
            else
                print_info "Kept NetworkManager configuration"
            fi
        else
            print_info "Kept NetworkManager configuration (use --clean to remove)"
        fi
    fi
}

cleanup_runtime_files() {
    print_step "Cleaning up runtime files..."
    
    # Remove any runtime state
    if [ -d "/run/vr-hotspot" ]; then
        rm -rf /run/vr-hotspot || true
    fi
    
    # Remove any temp files
    rm -rf /tmp/vr-hotspot-* 2>/dev/null || true
    rm -rf /dev/shm/lnxrouter_tmp* 2>/dev/null || true
    
    print_success "Runtime files cleaned"
}

check_remaining_files() {
    print_step "Checking for remaining files..."
    
    FOUND_FILES=0
    
    if [ -d "/var/lib/vr-hotspot" ]; then
        echo "  • /var/lib/vr-hotspot/ still exists"
        FOUND_FILES=1
    fi
    
    if [ -f "/etc/systemd/system/vr-hotspotd.service" ]; then
        echo "  • /etc/systemd/system/vr-hotspotd.service still exists"
        FOUND_FILES=1
    fi
    
    if [ $FOUND_FILES -eq 0 ]; then
        print_success "All files removed cleanly"
    else
        print_warning "Some files may remain"
    fi
}

show_completion() {
    clear
    print_header
    
    echo -e "${GREEN}${BOLD}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                  ✅ UNINSTALLATION COMPLETE                      ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
    
    print_success "VR Hotspot has been uninstalled"
    echo ""
    
    if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
        echo -e "${CYAN}${BOLD}💾 Configuration Backup:${NC}"
        echo "   $BACKUP_DIR"
        echo "   (Delete this folder if you don't need it)"
        echo ""
    fi
    
    echo -e "${CYAN}${BOLD}🧹 What Was Removed:${NC}"
    echo "   ✓ VR Hotspot daemon and service"
    echo "   ✓ Installation directory (/var/lib/vr-hotspot)"
    echo "   ✓ Systemd service files"
    echo "   ✓ Runtime and temporary files"
    echo ""
    
    if [ -f "/etc/NetworkManager/conf.d/vr-hotspot.conf" ]; then
        echo -e "${YELLOW}${BOLD}⚠️  Kept (not removed):${NC}"
        echo "   • NetworkManager configuration: /etc/NetworkManager/conf.d/vr-hotspot.conf"
        echo "   • Run again with manual removal if needed"
        echo ""
    fi
    
    echo -e "${CYAN}${BOLD}📦 Dependencies:${NC}"
    echo "   System packages (iw, python, etc) were NOT removed"
    echo "   They may be used by other applications"
    echo ""
    
    echo -e "${CYAN}${BOLD}🔄 To Reinstall:${NC}"
    echo "   curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh | sudo bash"
    echo ""
    
    echo -e "${GREEN}Thank you for using VR Hotspot! 👋${NC}"
    echo ""
}

# Main uninstallation flow
main() {
    clear
    print_header
    
    echo -e "${BOLD}This will uninstall VR Hotspot from your system.${NC}"
    echo ""
    
    # Pre-flight checks
    check_root
    
    if ! check_installation; then
        echo ""
        print_info "VR Hotspot may already be uninstalled or not properly installed."
        if [ "$INTERACTIVE" -eq 1 ]; then
            read -p "Continue anyway? (y/n) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                print_info "Uninstallation cancelled"
                exit 0
            fi
        fi
    fi
    
    # Show what will be removed
    show_what_will_be_removed
    
    if [ "$INTERACTIVE" -eq 1 ]; then
        echo -e "${RED}${BOLD}⚠️  This action cannot be undone!${NC}"
        echo ""
        read -p "Continue with uninstallation? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Uninstallation cancelled"
            exit 0
        fi
        echo ""
    fi
    
    # Uninstallation steps
    stop_services
    disable_services
    backup_config
    remove_service_files
    remove_installation_directory
    cleanup_runtime_files
    cleanup_networkmanager
    check_remaining_files
    
    # Show completion
    show_completion
}

# Handle command line arguments
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    print_header
    echo "Usage: sudo bash uninstall.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --help, -h     Show this help message"
    echo "  --clean        Remove all configuration including NetworkManager settings"
    echo ""
    echo "Examples:"
    echo "  sudo bash uninstall.sh"
    echo "  curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/uninstall.sh | sudo bash"
    echo ""
    exit 0
fi

# Run main uninstallation
main "$@"
