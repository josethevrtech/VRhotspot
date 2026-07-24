# VR Hotspot

VR Hotspot is an open-source connectivity suite for VR headsets that turns a PC into a dedicated Wi-Fi access point using a USB Wi-Fi adapter. It enables a direct, low-latency PC ↔ headset connection optimized for VR streaming and remote access, no router required.

It's ideal for users who travel with a MiniPC or "headless" computer puck and want confidence they can connect to and manage their PC's hotspot, even without a monitor, so they can connect a VR headset and stream to it.

Built around **lnxrouter + hostapd + dnsmasq**, with **bundled binaries** (including libnl) for consistent installs across distros, and integrates with **firewalld** on platforms like SteamOS where firewalld owns nftables.

---

## 🚀 Quick Installation

**SteamOS install**

For SteamOS, download the installer first and run it as a local file. Do not
use `curl | bash` on SteamOS.

### Guided install (recommended)

Use the guided installer for normal public SteamOS installs. `--interactive`
keeps the guided installer prompts enabled.

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
sudo bash /tmp/vrhotspot-install.sh --interactive
```

### Unattended install

Use unattended mode only for automation, support, or managed installs.
`--non-interactive` intentionally skips prompts and uses defaults.

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
sudo bash /tmp/vrhotspot-install.sh --non-interactive
```

### Optional Flatpak desktop companion

The guided installer asks `Install the Flatpak companion app?` and defaults to
No while the companion and its local packaging mature. Choosing No leaves the
existing daemon installation unchanged. Choosing Yes makes a best-effort,
user-scoped build/install from
`packaging/flatpak/io.github.josethevrtech.VRhotspot.json`.

Unattended installs do not install the companion by default. Explicitly opt in
with:

```bash
sudo bash /tmp/vrhotspot-install.sh --non-interactive \
  --install-flatpak-companion
```

The optional path requires `flatpak`, `flatpak-builder`, and the GNOME 50
runtime/SDK to already be available. It does not add Flathub or another Flatpak
remote. Missing prerequisites or a failed build are reported clearly, temporary
build files are removed, and the daemon install continues.

The desktop launcher starts the companion in tray mode. Its only graphical UI
is the daemon-served Web Portal in a locked WebKitGTK window. Primary tray
activation opens or restores that same window, and the window close action
hides it to the tray without creating another window. Redundant Show and Hide
menu commands are not exported. If WebKit cannot be constructed, the companion
shows a bounded GTK error surface and does not open another interface. The tray
keeps **Current status** visible at the top. **Hotspot Commands** groups Start,
Stop, Restart, and Repair; **Network** contains Share Internet Connection; and
**Advanced** contains Authentication, Refresh Status, Open Diagnostics,
Privacy Mode, and the existing Start Hotspot Automatically setting. That
setting controls daemon hotspot autostart, not desktop-companion login
autostart. Launching VR Hotspot at desktop login remains deferred and is not
shown as a tray item. Explicit Quit exits only the desktop companion and does
not stop an already-running hotspot.

The Web Portal shell and tray are one companion and share authentication state.
An API token accepted after explicit entry in the Portal is adopted immediately
by the tray. When Secret Service is available, the accepted token is saved in
the app-specific wallet; otherwise it remains in memory for the current
companion process only. A valid saved wallet token authenticates the tray at
launch and is supplied to the Portal only through a bounded, fixed-origin,
in-memory WebKit bridge—not through a URL, file, or command argument.

**Authentication…** remains available for explicit token entry, replacement,
testing, and clearing. The companion never uses sudo to obtain a token and
never discovers one from `/etc/vr-hotspot/env`, `/var/lib/vr-hotspot`, daemon
configuration, environment variables, or command arguments. Missing or
rejected credentials are reported as **Needs Authentication**, separately from
**Daemon Unavailable** and unexpected **Error** states. Needs Authentication is
a static icon state; working/pulsing indication is reserved for active
transitions. Start, Stop, Restart, and Repair remain disabled until the shared
token authenticates successfully and then become available according to the
real daemon state. The historical flag remains a compatibility alias for the
same default graphical behavior:

```bash
flatpak run io.github.josethevrtech.VRhotspot --web-portal-shell
```

Launching the tray companion at desktop login is not implemented and remains
distinct from starting the hotspot automatically with the computer.

**Other Linux distributions**

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
sudo bash /tmp/vrhotspot-install.sh
```

**Bazzite support policy**

Bazzite is a supported target through the dedicated `rpm-ostree` installer
path. VR Hotspot uses its bundled hostapd/dnsmasq stack on Bazzite instead of
layering system copies. If another required base tool is missing, the installer
first attempts live package layering; when that is unavailable, it stages the
packages and asks you to reboot and rerun the installer. The installer does not
reboot the system automatically.

**Features:**
- ✅ Auto-detects your OS (SteamOS, Bazzite, CachyOS, Arch, EndeavourOS, Ubuntu, Fedora)
- ✅ Installs required dependencies automatically where the platform permits it
- ✅ Configures NetworkManager to prevent interference
- ✅ Starts service and shows you the web UI URL and API token
- ✅ Perfect for beginners - no Linux knowledge required

**To uninstall:**

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/uninstall.sh -o /tmp/vrhotspot-uninstall.sh
sudo bash /tmp/vrhotspot-uninstall.sh
```

The daemon uninstaller leaves any user-scoped Flatpak companion and configured
Flatpak remotes untouched. Remove only this companion explicitly, if desired:

```bash
flatpak uninstall --user io.github.josethevrtech.VRhotspot
```

---

## Quick Start

Once installed, open the web UI:

- **Local Portal:** `http://127.0.0.1:8732`
- **From Another Device:** `http://<your-pc-ip>:8732`
- **Health Check:** `http://127.0.0.1:8732/healthz`

Enter the **API token** shown during installation to access the interface.

**Basic Usage:**
1. Open the web UI
2. Enter your API token
3. Select your Wi-Fi adapter (wlan1 recommended over wlan0)
4. Click **Start** to create your hotspot
5. Connect your VR headset to the new network

---

## Supported Wi-Fi Adapters

### ✅ Recommended (Tested & Working)
- **BrosTrend AXE3000 Tri-Band** (Best Choice) - https://www.amazon.com/dp/B0F6MY7H62
- **EDUP EP-AX1672** - https://www.amazon.com/EDUP-Wireless-802-11AX-Tri-Band-Compatible/dp/B0CVVWNSH2
- **Panda Wireless PAU0F AXE3000** - https://www.amazon.com/Panda-Wireless%C2%AE-PAU0F-AXE3000-Adapter/dp/B0D972VY9B

### ℹ️ Should Work (Untested)
- See compatible adapters list: https://github.com/morrownr/USB-WiFi/blob/main/home/USB_WiFi_Adapters_that_are_supported_with_Linux_in-kernel_drivers.md#axe3000---usb30---24-ghz-5-ghz-and-6-ghz-wifi-6e

### ⚠️ Known Issues
- **(wlan0)**: Built-in adapters often have AP mode limitations. Use wlan1+ (USB adapters) for better reliability.

---

## What It Does

- Creates a Wi-Fi hotspot (AP) from a selected Wi-Fi adapter
- Provides DHCP + DNS via bundled **dnsmasq**
- Enables NAT/forwarding so clients can reach the internet
- Exposes a web UI for easy configuration and management
- Includes **Repair** workflow to recover from stuck states
- Automatically prioritizes wlan1+ over wlan0 for better compatibility

---

## Key Features

### 🎮 VR-Optimized

- **Low-latency optimized** for VR streaming
- **QoS profiles**: Ultra Low Latency, High Throughput, Balanced, Stability (VR default)
- **Band preference**: 6 GHz → 5 GHz → 2.4 GHz with automatic fallback
- **Wi-Fi 6/6E support** with auto-detection
- **System tuning options**: CPU governor, power management, interrupt coalescing

### 🔧 Smart Adapter Management

- Auto-detects Wi-Fi adapters and recommends the best one
- **Prioritizes wlan1+** over wlan0 (avoids Intel AX200 issues)
- Hides problematic adapters in Basic Mode
- Supports multiple bands: 2.4 GHz, 5 GHz, 6 GHz (Wi-Fi 6E)

### 🌐 Web UI & API

**Lifecycle Controls:**
- Start / Stop / Repair / Restart
- `POST /v1/start`, `POST /v1/stop`, `POST /v1/repair`, `POST /v1/restart`
- `POST /v1/autostart` - Coordinate the existing hotspot boot-autostart unit
  and canonical `autostart` setting

**Status & Monitoring:**
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

### 🔥 Firewalld Integration (SteamOS-Friendly)

When `firewalld` is running, the daemon uses `firewall-cmd` (not raw nftables/iptables):
- Adds AP interface to trusted zone
- Enables masquerade/forwarding
- Optional cleanup on stop
- No conflicts with firewalld-managed systems

### 📦 Bundled Dependencies

- **hostapd** (v2.11) - AP management
- **dnsmasq** - DHCP/DNS server
- **lnxrouter** - Wrapper script
- **libnl** (v3.10) - Netlink library (no system packages needed!)

All binaries are bundled for consistent, portable installations.

---

## Performance Tuning (Optional)

Enable in the web UI under Advanced Mode:

**System Tuning:**
- `wifi_power_save_disable` - Disable power saving on Wi-Fi
- `cpu_governor_performance` - Set CPU to performance mode
- `usb_autosuspend_disable` - Prevent USB adapter suspension
- `sysctl_tuning` - Kernel network stack optimizations
- `interrupt_coalescing` - Optimize network interrupts
- `cpu_affinity` - Pin processes to specific CPU cores

**QoS Presets:**
- **Ultra Low Latency** - Strict priority + UDP optimization
- **Stability (VR)** - DSCP CS5 + cake qdisc (recommended for VR)
- **High Throughput** - DSCP AF42 + cake qdisc
- **Balanced** - DSCP AF41 + fq_codel

---

## Advanced Installation (Manual)

### For Developers or Custom Setups

**1. Clone the repository:**

```bash
git clone https://github.com/josethevrtech/VRhotspot.git
cd VRhotspot
```

**2. Copy to system location:**

```bash
sudo mkdir -p /var/lib/vr-hotspot/app
sudo rsync -a ./ /var/lib/vr-hotspot/app/
```

Note: Installed deployments serve WebUI assets from `/var/lib/vr-hotspot/app/assets`.
When running from the repo, the backend prefers `./assets` first.

**3. Run the install script:**

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh
```

**Optional flags:**
- `--bind 0.0.0.0` - Allow access from other devices
- `--enable-autostart` - Start hotspot automatically on boot
- `--api-token <token>` - Use a specific API token

**4. Verify installation:**

```bash
curl -fsS http://127.0.0.1:8732/healthz && echo OK
sudo systemctl status vr-hotspotd.service
```

---

## Configuration

### API Token

The install script generates a secure API token. To retrieve it:

```bash
sudo cat /etc/vr-hotspot/env
```

Look for:
```bash
VR_HOTSPOTD_API_TOKEN=<your-token>
```

### Firewall Ports

VR Hotspot listens on **TCP 8732**. The installer automatically opens this port in:
- firewalld (if active)
- ufw (if installed)

**Manual firewall configuration:**

```bash
# firewalld
sudo firewall-cmd --permanent --add-port=8732/tcp
sudo firewall-cmd --reload

# ufw
sudo ufw allow 8732/tcp
```

### Autostart on Boot

This means **Start Hotspot Automatically** with the computer. It is separate
from launching the desktop tray companion at login. The setting coordinates
the canonical `autostart` config value with the existing
`vr-hotspot-autostart.service`.

After installing the Flatpak companion, use the tray toggle. During daemon
installation or repair, use the existing installer options:

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh --enable-autostart
```

Disable autostart:

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh --disable-autostart
```

---

## Troubleshooting

### SteamOS Validation

SteamOS 3.8.12 stable has been validated working with this hotfix branch.

Validated result:
- SteamOS 3.8.12 stable
- bundled hostapd/dnsmasq/lnxrouter stack
- AP interface x0wlan1
- 5 GHz channel 36
- 80 MHz width
- client association and WPA handshake confirmed
- internet and streaming confirmed working

Validation checklist:

```bash
systemctl status vr-hotspotd.service --no-pager
ls -lah /var/lib/vr-hotspot/app/backend/vendor/bin
sudo grep -E 'VR_HOTSPOT.*VENDOR|VR_HOTSPOT_FORCE_VENDOR_BIN|VR_HOTSPOTD_HOST|VR_HOTSPOTD_PORT' /etc/vr-hotspot/env
curl -fsS http://127.0.0.1:8732/healthz && echo OK
iw dev
iw dev x0wlan1 station dump
```

### Check Service Status

```bash
sudo systemctl status vr-hotspotd.service
```

### View Logs

```bash
# Recent logs
sudo journalctl -u vr-hotspotd.service -n 100

# Follow logs in real-time
sudo journalctl -u vr-hotspotd.service -f
```

### Check API Status

```bash
# Get API token
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)

# Check status
curl -s "http://127.0.0.1:8732/v1/status?include_logs=1" -H "X-Api-Token: $TOKEN" | python3 -m json.tool
```

### Preflight Diagnostics

Installed systems provide a read-only CLI that calls the existing authenticated
preflight endpoint. Running it with `sudo` lets it read the protected daemon
token from `/etc/vr-hotspot/env` without putting the token in shell history or
process arguments:

```bash
# Print the canonical report as formatted JSON
sudo /var/lib/vr-hotspot/bin/vr-hotspot preflight

# Export through stdout into a new private, user-owned directory and file
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/vr-hotspot-preflight.XXXXXX")"
(umask 077; sudo /var/lib/vr-hotspot/bin/vr-hotspot preflight \
  > "$report_dir/preflight.json")
```

`--output PATH` is also available when the CLI should create the file directly.
It creates a new file with mode `0600` and refuses existing paths and symlinks;
choose a private destination rather than a predictable shared `/tmp` filename.

For development or custom API locations, use `--api-url` and provide the token
through `VR_HOTSPOTD_API_TOKEN` or stdin. For a one-off prompt that does not echo
the token or store it in shell history:

```bash
read -rsp 'VR Hotspot API token: ' API_TOKEN && echo
printf '%s\n' "$API_TOKEN" | vr-hotspot preflight \
  --api-url http://127.0.0.1:8732 --token-stdin
unset API_TOKEN
```

The `--token` option is available for automation compatibility, but its value is
visible in process arguments and may be retained in shell history; prefer the
protected env file, environment variable, or `--token-stdin`. The CLI rejects
redirects and only performs `GET /v1/diagnostics/preflight`; it does not probe
the host directly or mutate hotspot state.

### Common Issues

**1. No Wi-Fi adapters found:**
- Check: `iw dev`
- Ensure adapter supports AP mode: `iw list | grep -A10 "Supported interface modes"`

2. Hotspot times out (ap_ready_timeout):

- Check if NetworkManager is interfering: `nmcli device status | grep wlan`
- Try using `wlan1` instead of `wlan0`
- Check logs: `sudo journalctl -u vr-hotspotd.service -n 50`

3. Can't access web UI:
- Check firewall: `sudo firewall-cmd --list-ports` or `sudo ufw status`
- Verify service is running: `curl http://127.0.0.1:8732/healthz`

**4. Intel AX200 (wlan0) not working:**
- This is a known hardware limitation
- Use wlan1 (USB adapter) instead
- See: `docs/troubleshooting/BUNDLED_LIBNL_SETUP.md`

### Repair Function

If the hotspot gets stuck, use the **Repair** button in the web UI or:

```bash
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)
curl -X POST "http://127.0.0.1:8732/v1/repair" -H "X-Api-Token: $TOKEN"
```

### Support Bundle

For bug reports, use **Download support bundle** in Pro mode or call the
authenticated endpoint directly:

```bash
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)
curl -OJ "http://127.0.0.1:8732/v1/diagnostics/support_bundle" -H "X-Api-Token: $TOKEN"
```

The bundle is a sanitized `.zip` with version, status, adapter inventory, and
readiness data. Review it before attaching it to a public issue.

---

## Security & Privacy

### Privileged API Threat Model

The portal shell, static assets, favicon, and `/healthz` are public. Every
`/v1/*` route controls or exposes privileged daemon state and requires both a
configured `VR_HOTSPOTD_API_TOKEN` and a matching `X-Api-Token` or Bearer token.
If the daemon token is absent or blank, privileged requests fail closed with
HTTP 503 and `result_code: api_token_missing`; supplying a token in the browser
cannot repair a missing daemon configuration. Configure the token in the daemon
environment (normally `/etc/vr-hotspot/env`) and restart the service.

The daemon runs as root, so the token protects network mutation, diagnostics,
configuration, and passphrase-reveal operations from other local processes as
well as network clients. Tokenless non-loopback binds are refused. Remote access
still uses plain HTTP and is trusted-network-only: use it only on a network where
traffic cannot be observed or modified. This project does not currently provide
built-in TLS termination.

### API Token Protection

- **Treat the token like a password** - don't share it publicly
- **Token enforcement** prevents unauthorized access
- Regenerate token if compromised: Edit `/etc/vr-hotspot/env` and restart service

### Privacy Mode

- Enable **Privacy Mode** in the web UI when:
  - Screen sharing
  - Taking screenshots
  - Collecting logs for support
- Hides sensitive information (logs, client details, etc.)

### Remote Access

- By default, the web UI only listens on `127.0.0.1` (local only)
- To allow remote access: `sudo ./install.sh --bind 0.0.0.0`
- **Important**: A strong token is mandatory, but remote HTTP is still suitable
  only for a trusted network

---

## Project Layout

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

---

## Contributing

Issues and pull requests are welcome!

**When filing a bug, please include:**
- OS/distro + kernel version
- Wi-Fi adapter chipset/model
- A sanitized support bundle from Pro mode or
  `GET /v1/diagnostics/support_bundle`
- If you cannot generate a support bundle, include
  `sudo journalctl -u vr-hotspotd.service -n 200` and
  `curl http://127.0.0.1:8732/v1/status?include_logs=1`
- Redact any API tokens or passwords from manually collected output

See `CONTRIBUTING.md` for more details.

---

## License

MIT License. See `LICENSE.md`.

---

## Third-Party Notices

VR Hotspot bundles third-party binaries and libraries. See:
- `THIRD_PARTY_NOTICES.md` - License attributions
- `backend/vendor/README.md` - Version information
- `backend/vendor/licenses/` - Full license texts
- `docs/VENDOR_PROVENANCE_SBOM_PLAN.md` - Staged provenance, SBOM, and checksum-manifest plan

Bundled components:
- **hostapd** (BSD) - https://w1.fi/hostapd/
- **dnsmasq** (GPL-2.0+) - https://thekelleys.org.uk/dnsmasq/
- **lnxrouter** (LGPL-2.1+) - https://github.com/garywill/linux-router
- **libnl** (LGPL-2.1) - https://github.com/thom311/libnl

---

## Acknowledgments

Built with ❤️ for the VR community.

Special thanks to:
- The hostapd, dnsmasq, and linux-router projects
- All contributors and testers
- The SteamOS and CachyOS communities
