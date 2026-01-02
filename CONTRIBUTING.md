# Contributing to VR Hotspot

Thanks for helping improve VR Hotspot. This project targets SteamOS/Linux systems and bundles Wi-Fi tooling, so please follow the guidelines below.

## Prerequisites
- Python 3.9+ (3.11+ recommended)
- systemd (required for service installs)
- Root privileges for actual hotspot operations (adapter control, hostapd/dnsmasq)
- Optional: Wi-Fi 6E-capable adapter for 6 GHz testing

## Local development
### Run backend directly (no systemd)
From the repo root:
```bash
PYTHONPATH=backend \
  VR_HOTSPOTD_HOST=127.0.0.1 \
  VR_HOTSPOTD_PORT=8732 \
  python3 -m vr_hotspotd.main
```

### Run via systemd (uses installer)
```bash
sudo bash backend/scripts/install.sh --enable-autostart
sudo systemctl status vr-hotspotd.service
journalctl -u vr-hotspotd.service -f
```
To remove:
```bash
sudo bash backend/scripts/uninstall.sh
```

## Style and quality
- Python: `ruff check .`
- Shell: `shellcheck backend/scripts/*.sh`
- Tests: `pytest -q`

## Submitting changes
- Keep diffs focused and avoid changing API routes or response envelopes.
- Include relevant logs and system info in PRs and bug reports.
- If you updated behavior or install paths, update README and docs accordingly.

## Bug reports
Include:
- OS/distro and version
- Kernel version
- Wi-Fi chipset and driver
- Exact steps to reproduce
- Expected vs. actual behavior
- Logs (journalctl -u vr-hotspotd.service -n 200)

Never paste API tokens or passphrases in public issues.
