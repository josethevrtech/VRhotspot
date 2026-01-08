# VR Hotspot

**VR Hotspot** is an open-source connectivity suite for VR headsets, designed to deliver a seamless, low-latency PC ↔ headset network without relying on a router. It turns your PC into a dedicated Wi-Fi access point (AP) for a headset (or any client), creating a direct, reliable connection optimized for VR streaming and remote access.

It’s ideal for users who travel with a MiniPC or “headless” computer puck and want confidence they can connect to and manage their PC’s hotspot, even without a monitor!, so they can connect a VR headset and stream to it.

Built around **lnxrouter + hostapd + dnsmasq**, it supports **bundled binaries** (consistent installs across distros) and integrates with **firewalld** on platforms like SteamOS where firewalld owns nftables.

## Supported WIFI Adapters
-  (RECOMMENDED) BrosTrend AXE3000 Tri-Band Linux https://www.amazon.com/dp/B0F6MY7H62
-  EDUP EP-AX1672 https://www.amazon.com/EDUP-Wireless-802-11AX-Tri-Band-Compatible/dp/B0CVVWNSH2
-  Panda Wireless® PAU0F AXE3000 Tri Band https://www.amazon.com/Panda-Wireless%C2%AE-PAU0F-AXE3000-Adapter/dp/B0D972VY9B?th=1

## Untested WIFI Adapters that *should* work
- https://github.com/morrownr/USB-WiFi/blob/main/home/USB_WiFi_Adapters_that_are_supported_with_Linux_in-kernel_drivers.md#axe3000---usb30---24-ghz-5-ghz-and-6-ghz-wifi-6e
  
---

## What it does

- Creates a Wi-Fi hotspot (AP) from a selected Wi-Fi adapter
- Provides DHCP + DNS via **dnsmasq**
- Enables NAT/forwarding so the headset can reach the internet
- Exposes a local portal (web UI) for configuration and lifecycle management
- Supports a **Repair** workflow to clean up and reapply state if the system gets stuck

---

## Key features

### Lifecycle controls (API)

- Start / Stop / Repair:
  - `POST /v1/start`
  - `POST /v1/stop`
  - `POST /v1/repair`
- Status:
  - `GET /v1/status`
- Optional logs:
  - `GET /v1/status?include_logs=1`

### Diagnostics (API)

- Clients on the AP interface:
  - `GET /v1/diagnostics/clients`
- ICMP ping sample and statistics:
  - `POST /v1/diagnostics/ping`
- Ping under load (curl default, iperf3 optional):
  - `POST /v1/diagnostics/ping_under_load`

### Adapter intelligence

- Enumerates Wi-Fi adapters and recommends an AP adapter (when available)
- Allows adapter selection in the portal and persists it in config

### Band preference and safe fallback

- Band preference (e.g., 6 GHz / 5 GHz / 2.4 GHz) with safe fallback behavior
- Fallback chain: **6 → 5 → 2.4** (when a band fails to become “ready” within the configured timeout)
- Timeout controls to determine when the AP is considered “ready”

> Note: 6 GHz AP mode requires WPA3 SAE and compatible hardware/regulatory support. VR Hotspot will fall back when needed.

Wi-Fi 6 (802.11ax) is auto-enabled only on adapters that report 802.11ax support. You can override it per start via `/v1/start` with `wifi6: "auto" | true | false`.

### Firewalld integration (SteamOS-friendly)

When `firewalld` is running, the daemon applies policy via `firewall-cmd` (not raw nftables/iptables):

- Adds the AP interface to a configured zone (default: `trusted`)
- Enables masquerade/forwarding when enabled by config
- Optionally cleans up firewall state on stop

This avoids conflicts on platforms where firewalld is the authority on firewall state.

---

## Performance tuning (optional)

These system-level tweaks can improve stability and latency for VR streaming. They are **off by default** and best-effort.
Enable them in the UI or via `/v1/config`.

System tuning options:
- `wifi_power_save_disable`: turns off Wi-Fi power save on the AP interface (and physical adapter)
- `usb_autosuspend_disable`: disables autosuspend on USB Wi-Fi adapters
- `cpu_governor_performance`: sets CPU governor to `performance` while the hotspot runs
- `cpu_affinity`: pins hostapd/dnsmasq/engine to specific CPUs (e.g., `"2"` or `"2-3"` or `"2,4"`)
- `sysctl_tuning`: raises socket buffers and enables `bbr` + `fq` if supported

Notes:
- These changes are applied on start and reverted on stop/repair when possible.
- Some systems may block governor or sysctl changes; VR Hotspot will warn but continue.

## Hardware tips

- Prefer a dedicated 5/6 GHz adapter for the AP.
- PCIe/M.2 adapters are typically more stable than USB for sustained throughput.
- Use high-gain antennas and avoid placing the host inside cabinets or near metal.
- Keep the AP and headset within clear line-of-sight for best latency.

### Security and privacy

#### API token enforcement to manage Hotspot via webportal!

 `VR_HOTSPOTD_API_TOKEN` is set, all `/v1/*` web or app based endpoints require authentication via token.

Supported headers (either works):

- `X-Api-Token: <token>`
- `Authorization: Bearer <token>`

#### Privacy mode

- Masks SSID and passphrase
- Redacts `-p/--passphrase` arguments in the engine command display

### Bundled binaries for portability

Designed to work whether the system has `hostapd` / `dnsmasq` installed or not:

- Prefers system binaries when available
- Otherwise uses bundled copies from `backend/vendor/bin`

---

## Project layout

```text
.
├── backend/
│   ├── scripts/
│   │   ├── install.sh                    # Installer logic (systemd/env/bin)
│   │   ├── uninstall.sh                  # Uninstall/remove service + config (if supported)
│   │   └── vr-hotspot-autostart.sh       # Autostart helper (installed to /var/lib/vr-hotspot/bin)
│   ├── systemd/
│   │   ├── vr-hotspot-autostart.service  # Optional: start hotspot automatically on boot
│   │   └── vr-hotspotd.service           # Main daemon unit
│   ├── vendor/
│   │   └── bin/                          # Bundled binaries (dnsmasq/hostapd/hostapd_cli/lnxrouter)
│   └── vr_hotspotd/
│       ├── adapters/                     # Adapter enumeration + capability detection
│       ├── engine/                       # lnxrouter and 6 GHz hostapd engines
│       ├── api.py                        # REST API endpoints (/v1/*, /healthz)
│       ├── lifecycle.py                  # Start/stop/repair orchestration (serialized + reconciled)
│       ├── server.py                     # HTTP server wiring (UI + API)
│       └── main.py                       # Daemon entrypoint
├── pyproject.toml
└── vr-keygen/
    └── keygen.py                         # Optional: key/token helper (NOT WORKING YET! Do it manually using token generate command)
```

---

## Quick start

Once installed and running, open:

- Local Portal: `http://127.0.0.1:8732/ui`
- Portal From Another Device: `http://hotspotdeviceip:8732/ui`
- Health check: `http://127.0.0.1:8732/healthz`

API token enforcement is enabled, use the token printed by the install script and paste it into the **API token** field in the portal.
To access the portal from other devices, bind the daemon to a non-local address (see Remote portal access).

---

## Installation

### Expected layout on the target machine

After copying the project, the backend must be present at:

- `/var/lib/vr-hotspot/app/backend`

Install scripts assume:

- Daemon entrypoint: `python3 -m vr_hotspotd.main`
- Env file: `/etc/vr-hotspot/env`
- systemd unit: `/etc/systemd/system/vr-hotspotd.service`
- Autostart helper: `/var/lib/vr-hotspot/bin/vr-hotspot-autostart.sh`

### Copy the project to the target machine (all distros)

From your repo root:

```bash
sudo mkdir -p /var/lib/vr-hotspot/app
sudo rsync -a ./ /var/lib/vr-hotspot/app/
```

### Install (CachyOS and SteamOS Supported)

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
chmod +x install.sh
sudo ./install.sh
```

Optional: allow portal/API access from LAN or hotspot clients:

```bash
sudo ./install.sh --bind 0.0.0.0
```

Optional: enable autostart (recommended):

```bash
sudo ./install.sh --enable-autostart
```

What the installer does:

- Generates `/etc/vr-hotspot/env` (including a new API token unless you provide `--api-token`)
- Installs and enables `vr-hotspotd.service`
- Installs autostart helper to `/var/lib/vr-hotspot/bin/` when autostart is enabled
- Opens `8732/tcp` in firewalld if active, otherwise ufw if present
- Creates a systemd drop-in if you install to a non-default directory (so paths remain correct)

Verify:

```bash
curl -fsS http://127.0.0.1:8732/healthz && echo OK
sudo journalctl -u vr-hotspotd.service -b --no-pager -n 200 -o cat
```

---

## Token setup (API protection)

### Obtain the token

The install script generates a token by default (unless one already exists or you pass `--api-token`).
It prints the token at the end and writes it to:

```bash
sudo cat /etc/vr-hotspot/env
```

Look for:

```bash
VR_HOTSPOTD_API_TOKEN=...
```
## Firewall ports

VR Hotspot’s portal/API listens on:

- `TCP 8732`

Install scripts attempt to open this automatically:

- firewalld active → open via `firewall-cmd`
- otherwise ufw installed → allow via `ufw allow`

### Manual: firewalld

```bash
sudo firewall-cmd --add-port=8732/tcp
sudo firewall-cmd --permanent --add-port=8732/tcp
sudo firewall-cmd --reload
```

### Manual: ufw

```bash
sudo ufw allow 8732/tcp comment "VR Hotspot portal/API"
sudo ufw status verbose
```

---

## Autostart on boot

If enabled, VR Hotspot can automatically bring up the hotspot after boot.

Components:

- `backend/systemd/vr-hotspot-autostart.service`
- Autostart helper installed to: `/var/lib/vr-hotspot/bin/vr-hotspot-autostart.sh`

Check status:

```bash
sudo systemctl status vr-hotspot-autostart.service --no-pager -l
sudo journalctl -u vr-hotspot-autostart.service -b --no-pager -n 200 -o cat
```

---

## Troubleshooting

### Service status

```bash
sudo systemctl status vr-hotspotd.service --no-pager -l
```

### Logs

```bash
sudo journalctl -u vr-hotspotd.service -b --no-pager -n 200 -o cat
```

### API authorization checks

token enforcement is enabled, you should see:

- `401` without token
- `200` with token

```bash
curl -i http://127.0.0.1:8732/v1/status | head -n 5

TOKEN="$(sudo awk -F= '($1=="VR_HOTSPOTD_API_TOKEN"){gsub(/\r/,"",$2); print $2; exit}' /etc/vr-hotspot/env)"
curl -i http://127.0.0.1:8732/v1/status -H "X-Api-Token: $TOKEN" | head -n 5
```

### Include logs for debugging

```bash
TOKEN="$(sudo awk -F= '($1=="VR_HOTSPOTD_API_TOKEN"){gsub(/\r/,"",$2); print $2; exit}' /etc/vr-hotspot/env)"
curl -fsS "http://127.0.0.1:8732/v1/status?include_logs=1" -H "X-Api-Token: $TOKEN" | python3 -m json.tool
```

Diagnostics notes:
- ICMP may be blocked by host or client firewalls; ping results can be incomplete.
- `curl` load works without headset-side software; `iperf3` requires a reachable iperf3 server.

---

## Security and privacy notes

- **Token enforcement:** treat it like a password. Do not paste it into logs or screenshots.
- **Local-only UI:** The portal is intended to be accessed locally (e.g., `127.0.0.1`). If you bind to non-local addresses, use a firewall and keep token enforcement enabled.
- **Privacy mode:** Use privacy mode in the portal when screen sharing or collecting logs.

---

## License

MIT License. See `LICENSE.md`.

---

## Contributing

Issues and PRs are welcome.

When filing a bug, include:

- OS/distro + kernel version
- Wi-Fi adapter chipset/model
- `journalctl -u vr-hotspotd.service -b --no-pager -n 200 -o cat`
- `/v1/status?include_logs=1` output (redact secrets)

---

## Third-party notices

VR Hotspot bundles third-party binaries under `backend/vendor/bin/`. See `THIRD_PARTY_NOTICES.md` and `backend/vendor/README.md` for details and source/license references.
