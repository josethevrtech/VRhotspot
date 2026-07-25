# VR Hotspot

VR Hotspot turns your Linux PC into a dedicated Wi-Fi access point for your VR
headset using a USB Wi-Fi adapter. You get a direct, low-latency PC ↔ headset
connection optimized for VR streaming and remote access — no router required.

It's ideal if you travel with a MiniPC or "headless" computer puck and want to
connect a VR headset and stream to it, even without a monitor.

> **Release candidate:** VR Hotspot is currently at **v1.1.0-rc3**. Release
> candidates are close to final but may still receive fixes before the stable
> v1.1.0 release. Please report any issues you hit.

---

## Install

Run these two commands in a terminal:

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
sudo bash /tmp/vrhotspot-install.sh
```

The installer:

- Auto-detects your OS (SteamOS, Bazzite, CachyOS, Arch, EndeavourOS, Ubuntu, Fedora)
- Installs required dependencies automatically where the platform permits it
- Configures NetworkManager to prevent interference
- Starts the service and shows you the web UI URL and API token
- Is beginner-friendly — no Linux knowledge required

For guided prompts, add `--interactive`. For automation or managed installs,
see [Advanced installation](docs/advanced-install.md).

### SteamOS note

On SteamOS, always **download the installer first and run it as a local file**
(exactly as shown above). Do not pipe the script straight into bash
(`curl | bash`) on SteamOS.

---

## Open the Web UI

Once installed, open the web UI in a browser:

- **On the same PC:** `http://127.0.0.1:8732`
- **From another device:** `http://<your-pc-ip>:8732`

Then:

1. Enter your **API token** (shown at the end of installation — see
   [Retrieve your API token](#retrieve-your-api-token) if you lost it)
2. Select your Wi-Fi adapter (wlan1 recommended over wlan0)
3. Click **Start** to create your hotspot
4. Connect your VR headset to the new network

---

## Flatpak desktop companion (optional)

The installer can also set up an optional desktop tray app (Flatpak) for
starting, stopping, and monitoring the hotspot without opening a browser. The
guided installer asks whether to install it (default: No), and pairs it with
the daemon automatically when you say Yes.

See [Flatpak desktop companion](docs/flatpak-companion.md) for requirements,
pairing details, tray behavior, and how authentication is stored.

---

## Retrieve your API token

The installer generates a secure API token and shows it when installation
finishes. To see it again later:

```bash
sudo cat /etc/vr-hotspot/env
```

Look for the line:

```bash
VR_HOTSPOTD_API_TOKEN=<your-token>
```

Treat this token like a password — anyone with it can control your hotspot.

---

## Uninstall

```bash
sudo bash /var/lib/vr-hotspot/app/uninstall.sh
```

This runs the uninstaller that was installed with VRhotspot (the same command
shown on the installer completion screen). It removes the daemon and, if
present, the optional Flatpak companion and its app data. Shared Flatpak
runtimes and unrelated apps are never touched.
Details: [Flatpak desktop companion](docs/flatpak-companion.md).

**Fallback:** if `/var/lib/vr-hotspot/app/uninstall.sh` is missing or your
install is broken, download and run the uninstaller directly:

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/uninstall.sh -o /tmp/vrhotspot-uninstall.sh
sudo bash /tmp/vrhotspot-uninstall.sh
```

---

## Supported distros

- **SteamOS** (validated on 3.8.12 stable — see the
  [SteamOS note](#steamos-note) above)
- **Bazzite** — Bazzite is a supported target through the dedicated
  `rpm-ostree` installer path. VR Hotspot uses its
  bundled hostapd/dnsmasq stack on Bazzite instead of layering system copies.
  If another required base tool is missing, the installer first attempts live
  package layering; when that is unavailable, it stages the packages and asks
  you to reboot and rerun the installer.
  The installer never reboots your system automatically.
- **CachyOS, Arch, EndeavourOS**
- **Ubuntu**
- **Fedora**

More detail: [Platform compatibility guide](docs/PLATFORM_COMPATIBILITY.md).

You'll also need a Wi-Fi adapter that supports AP mode — see
[Supported Wi-Fi adapters](docs/wifi-adapters.md) for tested recommendations.

---

## Troubleshooting & advanced docs

Something not working? Start with the
[Troubleshooting guide](docs/troubleshooting.md) (service status, logs, common
issues, the Repair function, and support bundles).

Full documentation index: [docs/README.md](docs/README.md). Highlights:

- [Advanced installation & configuration](docs/advanced-install.md) — manual
  install, firewall ports, autostart, performance tuning
- [Supported Wi-Fi adapters](docs/wifi-adapters.md)
- [Security & privacy](docs/security.md)
- [Architecture overview](docs/architecture.md) — API surface, bundled vendor
  stack, project layout
- [Dev Bridge](docs/dev-bridge.md) — ADB helpers for headset developers
- [Contributing guide](CONTRIBUTING.md)

---

## License

MIT License. See `LICENSE.md`. Bundled third-party components are listed in
`THIRD_PARTY_NOTICES.md`.

Built with ❤️ for the VR community. Special thanks to the hostapd, dnsmasq,
and linux-router projects, all contributors and testers, and the SteamOS and
CachyOS communities.
