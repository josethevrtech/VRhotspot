#!/bin/bash
# Complete fix for wlan0 - Installs bundled libnl and configures NetworkManager
# Run this on the HOST system (Konsole on SteamOS, not VSCode)

set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║           VR Hotspot - Complete wlan0 Fix Script                ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Check we're in the right directory
if [ ! -f "backend/systemd/vr-hotspotd.service" ]; then
    echo "❌ Error: Run this from the VRhotspot project root directory"
    echo "   cd /home/deck/Projects/VRhotspot"
    exit 1
fi

# Check bundled libraries exist
if [ ! -f "backend/vendor/lib/libnl-3.so.200" ]; then
    echo "❌ Error: Bundled libnl libraries not found in backend/vendor/lib/"
    echo "   Make sure you have the updated project files."
    exit 1
fi

echo "✓ Project directory verified"
echo "✓ Bundled libnl libraries found"
echo ""

# Step 1: Reinstall daemon
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1: Reinstalling daemon with bundled libnl libraries"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

sudo bash backend/scripts/install.sh

if [ $? -ne 0 ]; then
    echo "❌ Installation failed!"
    exit 1
fi

echo ""
echo "✓ Daemon installed successfully"
echo ""

# Verify bundled libs were deployed
if [ ! -f "/var/lib/vr-hotspot/app/backend/vendor/lib/libnl-3.so.200" ]; then
    echo "❌ Warning: Bundled libraries not found at /var/lib/vr-hotspot/app/backend/vendor/lib/"
    echo "   Installation may have failed."
else
    echo "✓ Bundled libnl libraries deployed to /var/lib/vr-hotspot/app/backend/vendor/lib/"
fi

# Step 2: Configure NetworkManager
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2: Configuring NetworkManager to ignore wlan0"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

sudo mkdir -p /etc/NetworkManager/conf.d/

sudo tee /etc/NetworkManager/conf.d/vr-hotspot.conf > /dev/null << 'EOFNM'
[keyfile]
# Prevent NetworkManager from managing WiFi adapters used by VR Hotspot
unmanaged-devices=interface-name:wlan0;interface-name:wlan1
EOFNM

echo "✓ Created /etc/NetworkManager/conf.d/vr-hotspot.conf"
echo ""

# Check if NetworkManager is running
if systemctl is-active --quiet NetworkManager; then
    echo "Restarting NetworkManager..."
    sudo systemctl restart NetworkManager
    echo "✓ NetworkManager restarted"
else
    echo "ℹ NetworkManager not running (this is fine)"
fi

# Step 3: Restart VR Hotspot daemon
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 3: Restarting VR Hotspot daemon"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

sudo systemctl restart vr-hotspotd

if systemctl is-active --quiet vr-hotspotd; then
    echo "✓ VR Hotspot daemon is running"
else
    echo "❌ Warning: VR Hotspot daemon failed to start"
    echo "   Check logs with: sudo journalctl -u vr-hotspotd -n 50"
fi

# Step 4: Verify
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 4: Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check LD_LIBRARY_PATH in service
if systemctl cat vr-hotspotd.service | grep -q "LD_LIBRARY_PATH=/var/lib/vr-hotspot/app/backend/vendor/lib"; then
    echo "✓ Systemd service has LD_LIBRARY_PATH configured"
else
    echo "❌ Warning: LD_LIBRARY_PATH not found in systemd service"
fi

# Check bundled hostapd works
echo ""
echo "Testing hostapd with bundled libraries..."
if LD_LIBRARY_PATH=/var/lib/vr-hotspot/app/backend/vendor/lib \
   /var/lib/vr-hotspot/app/backend/vendor/bin/hostapd -v > /tmp/hostapd_test.txt 2>&1; then
    echo "✓ hostapd runs without crashing"
elif grep -q "hostapd v2" /tmp/hostapd_test.txt; then
    echo "✓ hostapd runs without crashing (exit code 1 is normal)"
else
    echo "❌ Warning: hostapd may have crashed"
    cat /tmp/hostapd_test.txt
fi

# Final summary
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                    ✅ INSTALLATION COMPLETE                      ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "What was done:"
echo "  ✓ Installed daemon with bundled libnl libraries"
echo "  ✓ Configured NetworkManager to ignore wlan0 and wlan1"
echo "  ✓ Restarted VR Hotspot daemon"
echo ""
echo "Next steps:"
echo "  1. Open web UI: http://localhost:8732"
echo "  2. Try starting hotspot on wlan0"
echo "  3. It should work now!"
echo ""
echo "To check logs if there are issues:"
echo "  sudo journalctl -u vr-hotspotd -f"
echo ""
echo "To verify no hostapd crashes:"
echo "  sudo journalctl -b | grep 'hostapd.*core'"
echo "  (should return nothing)"
echo ""
