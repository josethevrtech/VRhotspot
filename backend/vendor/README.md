# Bundled vendor binaries

This directory contains prebuilt binaries used by VR Hotspot to ensure consistent behavior across SteamOS/Linux environments where system packages may vary.

## Why binaries are bundled
- Avoids dependency on distro package versions and feature flags.
- Ensures hostapd/dnsmasq compatibility with 6 GHz and AP modes.
- Simplifies installation on SteamOS (immutable system areas).

## Update process
1) Choose upstream versions for `hostapd`, `dnsmasq`, and `linux-router`.
2) Replace binaries in `backend/vendor/bin/`.
3) Record versions and upstream commit/tag in this README.
4) Update license texts or references in `backend/vendor/licenses/`.
5) Update `THIRD_PARTY_NOTICES.md` if paths or licenses change.
6) Smoke test: start/stop hotspot and confirm DHCP + AP behavior.

## Version record
- dnsmasq: <record version/tag here>
- hostapd: <record version/tag here>
- linux-router (lnxrouter): <record version/tag here>
