# Third-Party Notices

VR Hotspot bundles third-party binaries under `backend/vendor/bin` for consistent behavior across SteamOS/Linux systems.

## Bundled components

### dnsmasq
- License: GPL v2/v3 (GPL-2.0-or-later)
- Binary path: `backend/vendor/bin/dnsmasq`
- Upstream: https://thekelleys.org.uk/dnsmasq/doc.html

### hostapd (and hostapd_cli)
- License: BSD (dual-license; see upstream COPYING)
- Binary paths: `backend/vendor/bin/hostapd`, `backend/vendor/bin/hostapd_cli`
- Upstream: https://w1.fi/hostapd/

### linux-router (lnxrouter)
- License: LGPL v2.1+ (LGPL-2.1-or-later)
- Binary path: `backend/vendor/bin/lnxrouter`
- Upstream: https://github.com/garywill/linux-router

## Corresponding source
Source for the exact versions of bundled binaries is available from the upstream projects. When updating binaries, record the exact upstream tag/commit and update `backend/vendor/README.md` and the license files under `backend/vendor/licenses/`. If you need the precise source for a shipped version, use the recorded tag/commit to fetch it from the upstream URLs above.
