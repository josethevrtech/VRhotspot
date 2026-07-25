# Architecture Overview

VR Hotspot is built around **lnxrouter + hostapd + dnsmasq**, with **bundled
binaries** (including libnl) for consistent installs across distros, and
integrates with **firewalld** on platforms like SteamOS where firewalld owns
nftables.

## What it does

- Creates a Wi-Fi hotspot (AP) from a selected Wi-Fi adapter
- Provides DHCP + DNS via bundled **dnsmasq**
- Enables NAT/forwarding so clients can reach the internet
- Exposes a web UI for easy configuration and management
- Includes **Repair** workflow to recover from stuck states
- Automatically prioritizes wlan1+ over wlan0 for better compatibility

## Key features

### VR-optimized

- **Low-latency optimized** for VR streaming
- **QoS profiles**: Ultra Low Latency, High Throughput, Balanced, Stability (VR default)
- **Band preference**: 6 GHz → 5 GHz → 2.4 GHz with automatic fallback
- **Wi-Fi 6/6E support** with auto-detection
- **System tuning options**: CPU governor, power management, interrupt coalescing

### Smart adapter management

- Auto-detects Wi-Fi adapters and recommends the best one
- **Prioritizes wlan1+** over wlan0 (avoids Intel AX200 issues)
- Hides problematic adapters in Basic Mode
- Supports multiple bands: 2.4 GHz, 5 GHz, 6 GHz (Wi-Fi 6E)

See `adapter-intelligence-v2.md` for the adapter readiness model design and
`wifi-adapters.md` for the recommended hardware list.

## Web UI & API surface

**Lifecycle controls:**
- Start / Stop / Repair / Restart
- `POST /v1/start`, `POST /v1/stop`, `POST /v1/repair`, `POST /v1/restart`
- `POST /v1/autostart` - Coordinate the existing hotspot boot-autostart unit
  and canonical `autostart` setting

**Status & monitoring:**
- `GET /v1/status` - Current hotspot status
- `GET /v1/config` - Current canonical configuration, including
  `enable_internet` and `autostart`
- `GET /v1/status?include_logs=1` - Status with logs
- `GET /v1/adapters` - List available Wi-Fi adapters
- `GET /v1/adapters/readiness` - Adapter Intelligence v2 readiness model

**Diagnostics:**
- `GET /v1/diagnostics/clients` - Connected clients
- `GET /v1/diagnostics/preflight` - Canonical read-only host readiness report
- `vr-hotspot preflight` - Print or export that same canonical report through the authenticated API
- `POST /v1/diagnostics/ping` - Ping test
- `POST /v1/diagnostics/ping_under_load` - Performance under load
- `GET /v1/diagnostics/support_bundle` - Download a sanitized support bundle
  (see `support-bundle.md`)

**ADB Dev Bridge (read-only, for standalone headset development):**
- `GET /v1/devbridge/status` - Hotspot/subnet state, host adb presence, and
  detected device counts
- `GET /v1/devbridge/devices` - Devices on the hotspot with an optional ADB
  TCP (port 5555) reachability check (`?probe=0` to skip)
- `GET /v1/devbridge/adb` - Copyable `adb connect`/pairing/logcat commands
  (`?ip=`, `?kind=connect|logcat|all`); the daemon never executes adb
- `GET /v1/devbridge/readiness` - Unity Build & Run readiness checks; every
  failed check includes a copyable next step
- `vr-hotspot devbridge status|scan|adb-command|logcat-command` - The same
  data through the authenticated CLI; see `dev-bridge.md`

## Firewalld integration (SteamOS-friendly)

When `firewalld` is running, the daemon uses `firewall-cmd` (not raw
nftables/iptables):

- Adds AP interface to trusted zone
- Enables masquerade/forwarding
- Optional cleanup on stop
- No conflicts with firewalld-managed systems

## Bundled dependencies (vendor stack)

- **hostapd** (v2.11) - AP management
- **dnsmasq** - DHCP/DNS server
- **lnxrouter** - Wrapper script
- **libnl** (v3.10) - Netlink library (no system packages needed!)

All binaries are bundled for consistent, portable installations.

### Third-party notices and provenance

VR Hotspot bundles third-party binaries and libraries. See:

- `../THIRD_PARTY_NOTICES.md` - License attributions
- `../backend/vendor/README.md` - Version information
- `../backend/vendor/licenses/` - Full license texts
- `VENDOR_PROVENANCE_SBOM_PLAN.md` - Staged provenance, SBOM, and
  checksum-manifest plan

Bundled components:

- **hostapd** (BSD) - https://w1.fi/hostapd/
- **dnsmasq** (GPL-2.0+) - https://thekelleys.org.uk/dnsmasq/
- **lnxrouter** (LGPL-2.1+) - https://github.com/garywill/linux-router
- **libnl** (LGPL-2.1) - https://github.com/thom311/libnl

## Project layout

```text
.
├── install.sh                          # One-command installer
├── uninstall.sh                        # One-command uninstaller
├── backend/
│   ├── scripts/
│   │   ├── install.sh                  # System installation script
│   │   ├── uninstall.sh                # System uninstallation script
│   │   └── vr-hotspot-autostart.sh     # Autostart helper
│   ├── systemd/
│   │   ├── vr-hotspotd.service         # Main daemon
│   │   └── vr-hotspot-autostart.service # Autostart service
│   ├── vendor/
│   │   ├── bin/                        # Bundled binaries
│   │   │   ├── hostapd
│   │   │   ├── dnsmasq
│   │   │   ├── hostapd_cli
│   │   │   └── lnxrouter
│   │   ├── lib/                        # Bundled libraries
│   │   │   ├── libnl-3.so.200
│   │   │   ├── libnl-genl-3.so.200
│   │   │   ├── libnl-route-3.so.200
│   │   │   └── libnl-cli-3.so.200
│   │   └── licenses/                   # Third-party licenses
│   └── vr_hotspotd/
│       ├── adapters/                   # Adapter detection & scoring
│       ├── engine/                     # AP engines (lnxrouter, hostapd6, bridge)
│       ├── diagnostics/                # Network diagnostics
│       ├── api.py                      # REST API
│       ├── lifecycle.py                # Start/stop/repair logic
│       ├── server.py                   # HTTP server
│       └── main.py                     # Entry point
├── assets/
│   ├── ui.js                           # Web UI JavaScript
│   ├── ui.css                          # Web UI styles
│   └── field_visibility.js             # UI field management
├── tests/                              # Test suite
└── pyproject.toml                      # Python package config
```
