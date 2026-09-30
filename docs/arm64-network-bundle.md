# Native ARM64 networking bundle (development)

The existing vendor executables are x86-64. This recipe builds native hostapd
2.11, hostapd_cli and dnsmasq 2.93 into a separate directory on ARM64 Linux. It
retains the existing linux-router shell script and does not replace checked-in
vendor files, install packages, configure networking or start a service.

This is a locally linked development bundle, not a universal ARM64 release.
It uses the builder's native libnl, OpenSSL and libc. The receipt records resolved
shared libraries and exact output hashes; other machines need compatible versions.
It does not add ARM support to the separately managed Android Platform-Tools bundle.

## Build

Use Python 3.12+, a C compiler, GNU make, pkg-config, ldd, and existing native libnl3
and OpenSSL development files. Run as a normal user. Prefer an isolated native
ARM64 builder when installing dependencies is necessary.

```sh
python3 tools/arm64/build.py /absolute/new/build-directory --jobs 4
```

The output directory must not exist. `--source-cache DIRECTORY` optionally uses
previously downloaded archives. Both cached and downloaded sources are SHA-256
checked before safe extraction. Pins and hostapd flags are version-controlled in
`tools/arm64/`. The initial pins record bytes downloaded from the upstream HTTPS
release sites; detached-signature authentication has not been performed. Hashes
prevent subsequent source drift, not proof of upstream authorship.

Upstream releases: [hostapd](https://w1.fi/releases/hostapd-2.11.tar.gz) and
[dnsmasq](https://thekelleys.org.uk/dnsmasq/). hostapd enables nl80211, 802.11n/ac/ax,
SAE, OWE, protected management frames and ACS. It does not claim hardware 6 GHz
qualification. DNS/DHCP uses dnsmasq's upstream default feature set; the exact
compile-time options appear in the version receipt.

Output includes `vendor/bin`, license notices, `vendor/BUILD_RECEIPT.json`, and
`sources/` containing the original archives plus build trees/configuration. Keep
the corresponding source and license materials when redistributing the binaries.
The linux-router script itself is its source. No prebuilt binaries are added to Git.

## Explicit runtime selection

After inspecting the receipt and verifying that the dependency paths are suitable
for the target, administrators can set these entries in the existing service
environment configuration:

```ini
VR_HOTSPOT_VENDOR_ROOT=/absolute/new/build-directory/vendor
VR_HOTSPOT_FORCE_VENDOR_BIN=1
```

The override must be an absolute directory containing `bin/`. It replaces the
vendor root for executable selection, profile selection, PATH and library lookup.
It never falls back to the repository's x86 vendor libraries. Remove any conflicting
`VR_HOTSPOT_FORCE_SYSTEM_BIN` setting before qualifying a vendor build. System
library compatibility remains necessary; a directory setting alone is not validation.
An invalid override fails rather than silently selecting the old bundle.

The root daemon's bundle must be deployed to an administrator-owned path before
production use. The build output is intentionally user-owned for development;
this document does not instruct starting a privileged daemon against that tree.
No service environment has been modified during initial validation. To roll back
a deployed configuration, restore its prior environment entries and restart the
service through the normal maintenance process. Do not remove an active bundle.

## Qualification status

On the ARM64 development laptop, all three ELF executables passed architecture,
version and shared-library checks. VRhotspot resolved the native hostapd/dnsmasq
paths. A real dnsmasq query/response succeeded on a temporary unprivileged loopback
port and the process was stopped afterward. This used no Wi-Fi interface and
changed no system resolver settings.

Remaining: root-owned deployment, hotspot start/stop on an AP-capable adapter,
Frame association and DHCP, offline Frame Control, reconnect/recovery and regulatory
band/channel testing. These are required before claiming travel-mode support.
