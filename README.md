# vr-hotspot

Production-oriented VR Wi-Fi hotspot daemon for SteamOS/Linux.

## Features (v1)
- Local-only HTTP API
- Adapter inventory
- Deterministic state
- Engine supervision (lnxrouter)
- Designed for VR tail-latency correctness

## Install (systemd)
Use the installer:
```bash
sudo bash backend/scripts/install.sh [options]
```

Paths and layout:
- Env file: /etc/vr-hotspot/env (VR_HOTSPOTD_HOST, VR_HOTSPOTD_PORT, optional VR_HOTSPOTD_API_TOKEN)
- Units: /etc/systemd/system/vr-hotspotd.service and /etc/systemd/system/vr-hotspot-autostart.service
- Autostart script: /var/lib/vr-hotspot/bin/vr-hotspot-autostart.sh
- Default app dir: /var/lib/vr-hotspot/app/backend

## 6 GHz notes
- 6 GHz uses a hostapd SAE engine and requires WPA3 SAE (set ap_security=wpa3_sae).

## Firewall notes
- On firewalld systems, forwarding/masquerade is handled via firewalld policy; the 6 GHz engine skips iptables NAT when firewalld is active.

## API
- GET /v1/status
- GET /v1/adapters

## Run
```bash
sudo vr-hotspotd
