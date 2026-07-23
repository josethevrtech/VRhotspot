# Platform Compatibility Guide

This project targets multiple Linux distros (Bazzite, CachyOS, SteamOS, Arch, EndeavourOS, Fedora, Ubuntu). The goal is to avoid fixes for one OS breaking another.

## Bazzite Support Policy

Bazzite is supported through its exact OS identifier and the specialized
`rpm-ostree` path. The installer forces the bundled hostapd/dnsmasq stack and
does not layer system copies of those two packages. Other missing base tools
may be installed with `rpm-ostree install --apply-live`; if live application
fails, the installer stages them and requires a user-managed reboot followed
by an installer rerun. It never orchestrates a reboot.

This policy does not add generic Fedora Atomic support. Fedora continues to use
the existing `dnf` dependency plan, while non-Bazzite Atomic variants remain
outside the installer OS mapping.

## Checklist for OS-Specific Changes

- [ ] Does the change modify `install.sh` or distro detection logic?
- [ ] Are package names/differences accounted for across apt/dnf/pacman/rpm-ostree?
- [ ] For SteamOS/CachyOS/Arch/EndeavourOS: do we keep the pacman-specific logic intact while treating immutable SteamOS separately?
- [ ] For Bazzite (rpm-ostree): do we preserve the bundled hostapd/dnsmasq policy and honest live-layering/reboot guidance?
- [ ] If a fix is OS-specific, is it guarded by an OS check or feature flag?
- [ ] For CachyOS: verify vendor hostapd/dnsmasq preference to avoid system package regressions.

## CI Smoke Checks

The CI job `install_matrix_check.sh` runs the installer in `--check-os` mode for each supported distro identifier. This verifies OS detection and dependency plan logic without modifying the system.

Run locally:

```bash
tools/ci/install_matrix_check.sh
```
