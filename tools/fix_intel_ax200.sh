#!/bin/bash
# Fix Intel AX200 (iwlwifi) AP mode issue
# Addresses "Could not set channel for kernel driver" error

set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║        Intel AX200 AP Mode Fix for wlan0                        ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

if [ "$EUID" -ne 0 ]; then 
    echo "❌ Error: This script must be run as root (use sudo)"
    exit 1
fi

echo "Device detected: Intel Wi-Fi 6 AX200 (iwlwifi driver)"
echo ""

# Step 1: Stop VR Hotspot
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1: Stopping VR Hotspot daemon"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
systemctl stop vr-hotspotd
echo "✓ Daemon stopped"
echo ""

# Step 2: Kill any remaining processes
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2: Cleaning up processes"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
killall hostapd 2>/dev/null || true
killall dnsmasq 2>/dev/null || true
killall wpa_supplicant 2>/dev/null || true
sleep 2
echo "✓ Processes cleaned up"
echo ""

# Step 3: Disconnect NetworkManager
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 3: Disconnecting NetworkManager from wlan0"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if command -v nmcli &> /dev/null; then
    nmcli device set wlan0 managed no 2>/dev/null || true
    nmcli device disconnect wlan0 2>/dev/null || true
    echo "✓ NetworkManager disconnected from wlan0"
else
    echo "ℹ NetworkManager not found (this is fine)"
fi
echo ""

# Step 4: Reset wlan0 interface
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 4: Resetting wlan0 interface"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ip link set wlan0 down
sleep 1
ip link set wlan0 up
sleep 2
echo "✓ wlan0 interface reset"
echo ""

# Step 5: Unload and reload iwlwifi driver (nuclear option)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 5: Reloading iwlwifi driver"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  Warning: This will disconnect all WiFi connections"
read -p "Continue? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Unloading iwlwifi module..."
    modprobe -r iwlwifi 2>/dev/null || echo "⚠️  Could not unload (may be in use)"
    sleep 2
    echo "Reloading iwlwifi module..."
    modprobe iwlwifi
    sleep 3
    echo "✓ Driver reloaded"
else
    echo "⊘ Skipped driver reload"
fi
echo ""

# Step 6: Configure NetworkManager permanently
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 6: Configuring NetworkManager permanently"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
mkdir -p /etc/NetworkManager/conf.d/
tee /etc/NetworkManager/conf.d/vr-hotspot.conf > /dev/null << 'EOF'
[keyfile]
# Prevent NetworkManager from managing WiFi adapters used by VR Hotspot
unmanaged-devices=interface-name:wlan0;interface-name:wlan1
EOF
echo "✓ Created /etc/NetworkManager/conf.d/vr-hotspot.conf"

if systemctl is-active --quiet NetworkManager; then
    systemctl restart NetworkManager
    echo "✓ NetworkManager restarted"
fi
echo ""

# Step 7: Wait for interface to stabilize
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 7: Waiting for wlan0 to stabilize"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
sleep 3
echo "✓ Interface ready"
echo ""

# Step 8: Restart VR Hotspot
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 8: Restarting VR Hotspot daemon"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
systemctl start vr-hotspotd
sleep 2

if systemctl is-active --quiet vr-hotspotd; then
    echo "✓ VR Hotspot daemon is running"
else
    echo "❌ Warning: Daemon failed to start"
    echo "   Check logs: sudo journalctl -u vr-hotspotd -n 50"
fi
echo ""

# Summary
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                    ✅ FIX APPLIED                                ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "What was done:"
echo "  ✓ Stopped all conflicting processes"
echo "  ✓ Disconnected NetworkManager from wlan0"
echo "  ✓ Reset wlan0 interface"
echo "  ✓ Optionally reloaded iwlwifi driver"
echo "  ✓ Configured NetworkManager to ignore wlan0 permanently"
echo "  ✓ Restarted VR Hotspot daemon"
echo ""
echo "Next steps:"
echo "  1. Open web UI: http://localhost:8732"
echo "  2. Try starting hotspot on wlan0"
echo ""
echo "If it still fails:"
echo "  • Check logs: sudo journalctl -u vr-hotspotd -f"
echo "  • Try using 5GHz instead of 2.4GHz (change in web UI)"
echo "  • Some Intel AX200 firmware versions have AP mode bugs"
echo "  • Check for firmware updates: sudo pacman -Syu"
echo ""
echo "Alternative: Use wlan1 instead (which already works)"
echo ""
