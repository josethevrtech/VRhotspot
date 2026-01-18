# Bundled vendor binaries

This directory contains prebuilt binaries used by VR Hotspot to ensure consistent behavior across SteamOS/Linux environments where system packages may vary.

## Why binaries are bundled
- Avoids dependency on distro package versions and feature flags.
- Ensures hostapd/dnsmasq compatibility with 6 GHz and AP modes.
- Simplifies installation on SteamOS (immutable system areas).

## OS-specific bundles
If an OS needs a different build, place binaries in OS-specific folders:
- `backend/vendor/bin/<profile>/` (hostapd, hostapd_cli, dnsmasq; lnxrouter optional)
- `backend/vendor/lib/<profile>/` (libnl and related shared libs)

Known profiles: `bazzite`, `steamos`, `cachyos`, `arch`, `fedora`.

Runtime behavior:
- Prefers OS-specific bundles when present.
- Falls back to the base `backend/vendor/bin/` and system binaries when not.
- Override selection with `VR_HOTSPOT_VENDOR_PROFILE=<profile>` and
  `VR_HOTSPOT_FORCE_VENDOR_BIN=1` in `/etc/vr-hotspot/env`.

## Update process
1) Choose upstream versions for `hostapd`, `dnsmasq`, and `linux-router`.
2) Replace binaries in `backend/vendor/bin/`.
3) Record versions and upstream commit/tag in this README.
4) Update license texts or references in `backend/vendor/licenses/`.
5) Update `THIRD_PARTY_NOTICES.md` if paths or licenses change.
6) Smoke test: start/stop hotspot and confirm DHCP + AP behavior.

## Version record
- dnsmasq: <record version/tag here>
- hostapd: v2.11 (hostap_2_11)
- hostapd (bazzite): <record version/tag here>
- linux-router (lnxrouter): v0.8.1
- libnl: 3.10 (libnl3_10_0) - bundled in vendor/lib/
