#!/bin/bash
# Project cleanup script - removes unnecessary files
# Review before running!

set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║              VR Hotspot Project Cleanup                          ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Function to prompt for confirmation
confirm() {
    read -p "$1 (y/n) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}

TOTAL_SAVED=0

# 1. Python cache files (~100KB)
echo "1. Python Cache Files"
echo "   These are automatically regenerated"
CACHE_SIZE=$(du -sh backend/vr_hotspotd/__pycache__ 2>/dev/null | cut -f1 || echo "0")
echo "   Size: $CACHE_SIZE"
if confirm "   Remove Python cache files?"; then
    find backend -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find backend -type f -name "*.pyc" -delete 2>/dev/null || true
    echo "   ✓ Removed"
else
    echo "   ⊘ Skipped"
fi
echo ""

# 2. get-pip.py (2.1MB) - only needed for initial setup
echo "2. get-pip.py (2.1MB)"
echo "   Only needed for initial pip installation"
if [ -f "get-pip.py" ]; then
    if confirm "   Remove get-pip.py?"; then
        rm get-pip.py
        echo "   ✓ Removed (saved 2.1MB)"
        TOTAL_SAVED=$((TOTAL_SAVED + 2100))
    else
        echo "   ⊘ Skipped"
    fi
else
    echo "   Already removed"
fi
echo ""

# 3. libnl source code directory (if it exists)
echo "3. libnl Source Code"
if [ -d "backend/vendor/bin/libnl-3.12.0" ]; then
    LIBNL_SIZE=$(du -sh backend/vendor/bin/libnl-3.12.0 2>/dev/null | cut -f1)
    echo "   Size: $LIBNL_SIZE"
    echo "   We only need the compiled .so files in vendor/lib/"
    if confirm "   Remove libnl source code?"; then
        rm -rf backend/vendor/bin/libnl-3.12.0
        echo "   ✓ Removed"
    else
        echo "   ⊘ Skipped"
    fi
else
    echo "   Already removed or not present"
fi
echo ""

# 4. Documentation files for troubleshooting (keep or archive)
echo "4. Temporary Troubleshooting Documentation"
echo "   These were created during debugging:"
echo "   - BUNDLED_LIBNL_SETUP.md (7KB)"
echo "   - SOLUTION_SUMMARY.md (4.5KB)"
echo "   - fix_wlan0.sh (6.4KB)"
echo "   - fix_intel_ax200_ap_mode.sh (7.7KB)"
echo ""
echo "   Options:"
echo "   a) Keep them (useful reference)"
echo "   b) Move to docs/ folder"
echo "   c) Delete them"
read -p "   Choose (a/b/c): " -n 1 -r
echo
case $REPLY in
    b|B)
        mkdir -p docs/troubleshooting
        mv BUNDLED_LIBNL_SETUP.md docs/troubleshooting/ 2>/dev/null || true
        mv SOLUTION_SUMMARY.md docs/troubleshooting/ 2>/dev/null || true
        mv fix_wlan0.sh docs/troubleshooting/ 2>/dev/null || true
        mv fix_intel_ax200_ap_mode.sh docs/troubleshooting/ 2>/dev/null || true
        echo "   ✓ Moved to docs/troubleshooting/"
        ;;
    c|C)
        rm -f BUNDLED_LIBNL_SETUP.md SOLUTION_SUMMARY.md fix_wlan0.sh fix_intel_ax200_ap_mode.sh
        echo "   ✓ Deleted"
        ;;
    *)
        echo "   ⊘ Kept in root"
        ;;
esac
echo ""

# 5. Git-ignored build artifacts
echo "5. Build Artifacts"
if [ -d ".pytest_cache" ]; then
    echo "   Found .pytest_cache"
    if confirm "   Remove pytest cache?"; then
        rm -rf .pytest_cache
        echo "   ✓ Removed"
    fi
fi
if [ -d "*.egg-info" ]; then
    echo "   Found .egg-info"
    if confirm "   Remove egg-info?"; then
        rm -rf *.egg-info
        echo "   ✓ Removed"
    fi
fi
echo ""

# Summary
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                    Cleanup Complete                              ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Project is now clean!"
echo ""
echo "To rebuild Python cache (if needed):"
echo "  python3 -m compileall backend/vr_hotspotd"
echo ""
