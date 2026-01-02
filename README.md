# VR Hotspot

**VR Hotspot** is an open-source connectivity suite optimized for VR headsets. It turns a Linux machine into a dedicated Wi-Fi access point (AP) for a headset (or any client) to create a seamless, low-latency PC ↔ VR network without relying on a router.

It provides a clean, one-button control plane for **Start / Stop / Repair**, adapter selection, safe diagnostics, and optional boot autostart.

Built around **lnxrouter + hostapd + dnsmasq**, it supports **bundled binaries** (consistent installs across distros) and integrates with **firewalld** on platforms like SteamOS where firewalld owns nftables.

---

## What it does

- Creates a Wi-Fi hotspot (AP) from a selected Wi-Fi adapter
- Provides DHCP + DNS via **dnsmasq**
- Enables NAT/forwarding so the headset can reach the internet (when configured)
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

### Adapter intelligence

- Enumerates Wi-Fi adapters and recommends an AP adapter (when available)
- Allows adapter selection in the portal and persists it in config

### Band preference and safe fallback

- Band preference (e.g., 6 GHz / 5 GHz / 2.4 GHz) with safe fallback behavior
- Fallback chain: **6 → 5 → 2.4** (when a band fails to become “ready” within the configured timeout)
- Timeout controls to determine when the AP is considered “ready”

> Note: 6 GHz AP mode requires WPA3 SAE and compatible hardware/regulatory support. VR Hotspot will fall back when needed.

### Firewalld integration (SteamOS-friendly)

When `firewalld` is running, the daemon applies policy via `firewall-cmd` (not raw nftables/iptables):

- Adds the AP interface to a configured zone (default: `trusted`)
- Enables masquerade/forwarding when enabled by config
- Optionally cleans up firewall state on stop

This avoids conflicts on platforms where firewalld is the authority on firewall state.

### Security and privacy

#### Optional API token enforcement

When `VR_HOTSPOTD_API_TOKEN` is set, all `/v1/*` endpoints require authentication.

Supported headers (either works):

- `X-Api-Token: <token>`
- `Authorization: Bearer <token>`

#### Privacy mode (portal)

- Masks SSID and passphrase
- Redacts `-p/--passphrase` arguments in the engine command display

### Bundled binaries for portability

Designed to work whether the system has `hostapd` / `dnsmasq` installed or not:

- Prefers system binaries when available
- Otherwise uses bundled copies from `backend/vendor/bin`

This makes deployments repeatable: copy the folder, run the install script, and installs behave consistently.

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
    └── keygen.py                         # Optional: key/token helper
```

---

## Quick start

Once installed and running, open:

- Portal: `http://127.0.0.1:8732/ui`
- Health check: `http://127.0.0.1:8732/healthz`

If API token enforcement is enabled, use the token printed by the install script and paste it into the **API token** field in the portal.

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

### Install (generic)

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
chmod +x install.sh
sudo ./install.sh
```

Optional: enable autostart:

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

The install script prints the token at the end and writes it to:

```bash
sudo cat /etc/vr-hotspot/env
```

Look for:

```bash
VR_HOTSPOTD_API_TOKEN=...
```

### Use the token with curl

```bash
TOKEN="$(sudo awk -F= '($1=="VR_HOTSPOTD_API_TOKEN"){gsub(/\r/,"",$2); print $2; exit}' /etc/vr-hotspot/env)"

curl -fsS http://127.0.0.1:8732/v1/status -H "X-Api-Token: $TOKEN"
curl -fsS http://127.0.0.1:8732/v1/status -H "Authorization: Bearer $TOKEN"
```

---

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

## Optional: Autostart on boot

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

If token enforcement is enabled, you should see:

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

---

## Security and privacy notes

- **Token enforcement:** If `VR_HOTSPOTD_API_TOKEN` is set, treat it like a password. Do not paste it into logs or screenshots.
- **Local-only UI:** The portal is intended to be accessed locally (e.g., `127.0.0.1`). If you bind to non-local addresses, use a firewall and keep token enforcement enabled.
- **Privacy mode:** Use privacy mode in the portal when screen sharing or collecting logs.

---

## License

MIT License. See `LICENSE.md` (or rename it to `LICENSE` if you prefer the common convention).

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

VR Hotspot may bundle third-party binaries under `backend/vendor/bin/`. Their license terms apply to those components. Consider adding a `THIRD_PARTY_NOTICES.md` documenting each bundled component, upstream source, and license.
