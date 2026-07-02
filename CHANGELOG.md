# Changelog

All notable VR Hotspot release planning and release notes are tracked here.

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

## Unreleased - v1.1.0

Planning status: proposed major update. The current stable release remains
`v1.0.4`; do not mark the software as `v1.1.0` until the release checklist is
complete.

Theme: "It Just Works" update for SteamOS, Bazzite, CachyOS, Arch, Ubuntu,
Fedora, and Pop!_OS users.

### Completed v1.1.0 work in this branch

- Add authenticated `GET /v1/adapters/readiness` endpoint for Adapter
  Intelligence v2 summaries, reason codes, Basic Mode recommendation data, and
  no-adapter responses.
- Add Adapter Readiness panels to Basic and Pro web UI surfaces.
- Add authenticated `GET /v1/diagnostics/support_bundle` endpoint that returns
  a sanitized `.zip` support bundle with version, status, adapter inventory, and
  readiness data.
- Add Pro web UI support-bundle download action.
- Add support bundle redaction, manifest, archive assembly, API, and UI
  contract tests.

### Installer reliability

- Plan installer hardening for distro detection, dependency checks, service
  setup, firewall integration, and recovery from partial installs.
- Improve install and uninstall output so beginners can see exactly what was
  configured and how to open the web UI.
- Define validation steps for SteamOS, Bazzite, CachyOS, Arch, Ubuntu, Fedora,
  and Pop!_OS install paths.

### Adapter Intelligence v2

- Plan a second-generation adapter scoring model that explains which adapter is
  recommended and why.
- Expand detection for AP mode support, band support, driver constraints,
  virtual adapters, and known weak built-in adapters.
- Preserve Basic Mode defaults that guide users toward the most reliable USB
  adapter without hiding useful advanced diagnostics.

### Wi-Fi 6E readiness diagnostics

- Plan diagnostics that report whether the system, adapter, regulatory domain,
  driver, and selected channel are ready for 6 GHz operation.
- Add clear readiness states for supported, blocked, degraded, and unknown
  environments.
- Document fallback expectations when 6 GHz is unavailable and 5 GHz or 2.4 GHz
  should be used instead.

### First-run setup wizard

- Plan a web UI wizard for first launch that walks users through token entry,
  adapter selection, SSID/passphrase setup, band choice, and first hotspot
  start.
- Keep the wizard focused on successful setup while leaving advanced controls
  available after onboarding.
- Include failure paths for missing adapters, blocked permissions, firewall
  issues, and unsupported 6 GHz operation.

### Support bundle export

- Plan a one-click support bundle that collects relevant configuration,
  platform facts, service status, recent logs, adapter inventory, diagnostics,
  and sanitized environment metadata.
- Ensure exported bundles avoid secrets, API tokens, passphrases, and private
  keys.
- Define CLI and web UI entry points so support data can be collected even when
  one surface is unavailable.

### Documentation refresh

- Refresh user-facing setup docs around supported distributions, recommended
  adapters, first-run expectations, recovery workflows, and diagnostics.
- Keep versioning guidance explicit: `v1.0.4` remains stable until the release
  is intentionally bumped.
- Update troubleshooting docs with clearer install, firewall, adapter, and
  Wi-Fi 6E readiness decision trees.

### Test coverage

- Plan coverage for installer preflight behavior, adapter scoring, Wi-Fi 6E
  readiness states, wizard payloads, support bundle sanitization, and release
  metadata drift prevention.
- Keep existing `PYTHONPATH=backend pytest` as the required full-suite check
  before release.
- Add distro-focused verification notes for SteamOS, Bazzite, CachyOS, Arch,
  Ubuntu, Fedora, and Pop!_OS.
