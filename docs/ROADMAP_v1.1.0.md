# VR Hotspot v1.1.0 Roadmap

Planning status: unreleased. The current stable release is `v1.0.4`; this
document does not mark the software as `v1.1.0` and does not require runtime
version metadata changes.

## Theme

The v1.1.0 "It Just Works" update is planned to make VR Hotspot easier to
install, diagnose, and recover across SteamOS, Bazzite, CachyOS, Arch, Ubuntu,
Fedora, and Pop!_OS.

The release should reduce first-run uncertainty for users who want a dependable
VR access point from a Linux PC or handheld without needing to understand
hostapd, dnsmasq, NetworkManager, firewalld, regulatory domains, or adapter
driver details.

## Goals

- Make installation and first launch clearer, more resilient, and easier to
  verify.
- Recommend the best available Wi-Fi adapter with transparent reasoning.
- Explain Wi-Fi 6E readiness before users spend time debugging 6 GHz failures.
- Add a guided first-run web UI path for successful hotspot creation.
- Provide a sanitized support bundle for faster troubleshooting.
- Refresh docs and tests so release quality is visible before tagging.

## Non-Goals

- Do not bump `pyproject.toml` or `backend/vr_hotspotd/__init__.py` during
  planning.
- Do not mark `v1.1.0` as released until implementation, testing, and release
  checklist work are complete.
- Do not remove advanced controls that power users already rely on.
- Do not make distro-specific behavior opaque; explain platform decisions where
  the system can detect them.

## Current Implementation Snapshot

This branch includes early v1.1.0 implementation work while the release remains
unreleased:

- `GET /v1/adapters/readiness` exposes Adapter Intelligence v2 readiness data
  from the existing adapter inventory.
- Basic and Pro UI surfaces show Adapter Readiness summaries.
- `GET /v1/diagnostics/support_bundle` returns a sanitized `.zip` support
  bundle with VR Hotspot version, status, adapter inventory, and readiness JSON.
- Pro UI includes a support bundle download action.

The installer hardening, first-run wizard, full CLI support-bundle helper, and
full platform collector set still need completion before release.

## Phase 1: Release Hygiene and Installer UX

- Audit release metadata so the project consistently reports `v1.0.4` until the
  final version bump.
- Improve installer messages for detected distro, dependency decisions, service
  setup, firewall actions, generated token location, and web UI URL.
- Harden partial-install recovery and uninstall paths.
- Validate installer behavior on SteamOS, Bazzite, CachyOS, Arch, Ubuntu,
  Fedora, and Pop!_OS.
- Define acceptance tests for install success, install failure messaging, repair
  guidance, and uninstall cleanup.

## Phase 2: Adapter Intelligence v2

- Design an adapter scoring model that considers interface role, AP support,
  band support, driver capability, known chipset behavior, and current system
  constraints.
- Explain recommendations in the API and web UI with concise reasons such as
  "USB adapter", "supports AP mode", "supports 6 GHz", or "built-in adapter
  deprioritized".
- Preserve advanced visibility into adapters that are not recommended.
- Add tests for adapter inventory normalization, scoring decisions, fallback
  behavior, and Basic Mode filtering.

## Phase 3: Wi-Fi 6E Readiness Endpoint

- Add a readiness model that reports whether 6 GHz operation is supported,
  blocked, degraded, or unknown.
- Check adapter capabilities, kernel/driver signals, regulatory domain, channel
  selection, hostapd compatibility, and platform constraints where detectable.
- Return actionable explanations and suggested fallback bands.
- Cover SteamOS, Bazzite, CachyOS, Arch, Ubuntu, Fedora, and Pop!_OS in manual
  verification notes where automated tests cannot fully represent hardware.

## Phase 4: First-Run Web UI Wizard

- Add a first-run path for token entry, adapter selection, SSID/passphrase
  setup, band selection, optional autostart, and first hotspot start.
- Use Adapter Intelligence v2 and Wi-Fi 6E readiness results to preselect
  sensible defaults.
- Include recovery paths for missing adapters, unsupported AP mode, firewall
  setup issues, invalid passphrases, and failed starts.
- Ensure returning users can skip the wizard and continue using the existing
  control surface.

## Phase 5: Support Bundle Export

- Define support bundle contents: platform facts, package/version metadata,
  service status, sanitized configuration, adapter inventory, diagnostic
  results, recent daemon logs, firewall mode, and installer state.
- Redact API tokens, hotspot passphrases, private keys, IPs where appropriate,
  and other sensitive environment values.
- Provide both web UI and CLI export paths.
- Add tests for redaction, expected file structure, failure tolerance, and
  bundle generation without elevated access where possible.

## Phase 6: Docs, Tests, and Release Checklist

- Refresh README setup flow, platform compatibility notes, adapter guidance,
  troubleshooting, versioning guidance, and diagnostics documentation.
- Add test coverage for installer UX helpers, Adapter Intelligence v2, Wi-Fi 6E
  readiness states, first-run wizard payloads, support bundle sanitization, and
  release metadata consistency.
- Run `PYTHONPATH=backend pytest` before release candidate tagging.
- Complete a release checklist that confirms no version metadata was bumped
  prematurely, then performs the intentional release bump only when ready.
- Publish final release notes from `CHANGELOG.md` after implementation and
  verification are complete.

## Release Checklist Draft

- `CHANGELOG.md` has final v1.1.0 notes and no planning-only placeholders.
- `docs/versioning.md` reflects the release source of truth.
- `pyproject.toml`, `backend/vr_hotspotd/__init__.py`, UI fallback metadata, and
  version metadata tests are updated together only at release time.
- Full suite passes with `PYTHONPATH=backend pytest`.
- Installer smoke tests pass on the target distro set.
- Support bundle redaction tests pass.
- GitHub Release notes match the final changelog.
