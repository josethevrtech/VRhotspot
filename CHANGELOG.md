# Changelog

All notable VR Hotspot release planning and release notes are tracked here.

## Unreleased - v1.1.0

`v1.1.0-rc4` is the final public validation release before stable v1.1.0. No
additional features are planned for this release line. Remaining changes should
be limited to release-blocking fixes found during clean-install and distro
validation.

## v1.1.0-rc4 - Developer Hub

This is the release where VRhotspot stops being just a dedicated hotspot and
becomes a complete Linux VR development workflow.

### Automatic Quest wireless development

- Add automatic Quest Wi-Fi enrollment over authorized USB ADB.
- Invoke Horizon OS `cmd wifi connect-network` directly instead of trusting its
  incomplete help output or scan results.
- Treat `Connection initiated` only as acknowledgment and wait for real
  association, DHCP, SSID, subnet, gateway, interface, and route verification.
- Retry automatic enrollment once before falling back to manual setup.
- Enable `adb tcpip` and connect to the headset only after the dedicated
  VRhotspot network has been fully verified.
- Keep the existing manual Horizon OS flow as a fallback with privacy-aware
  credential assistance and automatic background rechecks.
- Abort safely if USB disconnects before verification.

### Developer Hub

- Replace permanent manual ADB forms with a guided five-stage workflow:
  Connect USB, Approve debugging, Join VRhotspot, Enable wireless, Complete.
- Add device discovery, device overview, APK upload, install, launch, stop,
  clear-data, and uninstall operations.
- Add managed Android Platform-Tools installation, repair, reinstall, and
  removal using the reviewed r37.0.0 pin.
- Detect system ADB ownership and allow guarded removal only on supported
  mutable hosts.
- Hide raw healthy ADB states and present useful user-facing status instead.
- Keep the browser out of hotspot secret handling during automatic enrollment.

### Desktop and portal experience

- Add the optional Flatpak desktop companion with the Web Portal built in.
- Add browser-session authentication shared by the window and tray surfaces.
- Add tray controls, automatic pairing, token handoff, app icon fixes, and
  install/uninstall cleanup.
- Rebuild Basic and Pro hotspot lifecycle presentation around one canonical
  state model: Starting, Running, Stopping, Stopped, Restarting, Repairing, and
  Needs attention.
- Add a local Wi-Fi state icon with reduced-motion support.

### Diagnostics and platform readiness

- Add Adapter Intelligence v2 readiness summaries and recommendations.
- Add canonical preflight diagnostics and platform-aware next steps.
- Add sanitized support-bundle generation and authenticated download.
- Add Wi-Fi 6E readiness reporting and safe fallback guidance.
- Add vendor provenance, checksum validation, deterministic SBOM generation,
  and support-bundle provenance data.

### Security and installer reliability

- Harden API authentication, browser sessions, input validation, secret
  redaction, subprocess argument construction, and wireless-ADB network policy.
- Keep wireless ADB fail-closed outside the active VRhotspot network.
- Exclude `node_modules`, virtual environments, caches, bytecode, and coverage
  output from backend deployment.
- Replace recursive application copying with a filtered tar sync.
- Restore previously active services when installation fails after shutdown.
- Add installer copy-safety, lifecycle DOM, wireless enrollment, secret
  confinement, and rollback regressions to CI.

### Validation

- Validated the full GUI workflow on a real Quest 3S connected to a CachyOS
  host: automatic Wi-Fi enrollment, DHCP, strict network verification,
  wireless ADB handoff, and continued operation after USB removal.
- Validated the portal remotely from SteamOS through an SSH tunnel.
- GitHub Actions covers Python 3.11, 3.12, and 3.13, full Pytest, Ruff,
  compileall, ShellCheck, vendor/SBOM validation, Platform-Tools pin validation,
  DOM integration, lifecycle regressions, and the installer matrix.

## v1.0.5 - SteamOS 3.8.12 Hotfix

- Update public SteamOS instructions to recommend guided interactive install by
  default, with non-interactive documented only for unattended automation and
  support workflows.
- Document SteamOS validation checks for the service, bundled vendor binaries,
  vendor-related environment settings, health endpoint, wireless interfaces,
  and client station association.
- Record the validated SteamOS result: bundled hostapd/dnsmasq/lnxrouter stack,
  AP interface `x0wlan1`, 5 GHz channel 36, 80 MHz width, confirmed client
  association and WPA handshake, and working internet plus streaming.
