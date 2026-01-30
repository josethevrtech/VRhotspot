# Platform Compatibility Guide

This project targets multiple Linux distros (Bazzite, CachyOS, SteamOS, Arch, Fedora, Ubuntu). The goal is to avoid fixes for one OS breaking another.

## Checklist for OS-Specific Changes

- [ ] Does the change modify `install.sh` or distro detection logic?
- [ ] Are package names/differences accounted for across apt/dnf/pacman/rpm-ostree?
- [ ] For SteamOS/CachyOS/Arch: do we keep the pacman-specific logic intact?
- [ ] For Bazzite (rpm-ostree): do we avoid breaking vendor-bundle or live-layering logic?
- [ ] If a fix is OS-specific, is it guarded by an OS check or feature flag?

## CI Smoke Checks

The CI job `install_matrix_check.sh` runs the installer in `--check-os` mode for each supported distro identifier. This verifies OS detection and dependency plan logic without modifying the system.

Run locally:

```bash
tools/ci/install_matrix_check.sh
```
