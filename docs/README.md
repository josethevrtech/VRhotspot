# VR Hotspot Documentation

Start with the main [README](../README.md) for installation and everyday use.
The documents here cover advanced usage, design, and development.

## User guides

- [Troubleshooting](troubleshooting.md) — service status, logs, common issues,
  repair, support bundles, SteamOS validation checklist
- [Supported Wi-Fi adapters](wifi-adapters.md) — recommended hardware and known
  issues
- [Advanced installation & configuration](advanced-install.md) — manual
  install, installer flags, API token, firewall ports, autostart, performance
  tuning
- [Flatpak desktop companion](flatpak-companion.md) — installing, pairing,
  tray behavior, authentication, uninstall behavior
- [Security & privacy](security.md) — API threat model, token protection,
  Privacy Mode, remote access
- [Dev Bridge](dev-bridge.md) — read-only ADB helpers for standalone headset
  development

## Design & internals

- [Architecture overview](architecture.md) — what the daemon does, API
  surface, bundled vendor stack, project layout
- [Adapter Intelligence v2](adapter-intelligence-v2.md) — adapter readiness
  model design
- [Support bundle design](support-bundle.md) — collection scope, redaction,
  output format
- [Platform compatibility guide](PLATFORM_COMPATIBILITY.md) — per-distro
  policies (including Bazzite) and OS-change checklist
- [Vendor provenance & SBOM plan](VENDOR_PROVENANCE_SBOM_PLAN.md)
- [Flatpak architecture plan](FLATPAK_ARCHITECTURE_PLAN.md)
- [First-run wizard](first-run-wizard.md)
- [Host facts snapshot plan](HOST_FACTS_SNAPSHOT_PLAN.md)
- [Versioning](versioning.md)
- [Roadmap v1.1.0](ROADMAP_v1.1.0.md)
- [Existing feature audit](EXISTING_FEATURE_AUDIT.md)

## Contributing

- [Contributing guide](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
- [Changelog](../CHANGELOG.md)
- [Bundled libnl setup](../BUNDLED_LIBNL_SETUP.md)
- [Third-party notices](../THIRD_PARTY_NOTICES.md)
