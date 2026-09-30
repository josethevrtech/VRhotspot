# Steam Frame Wireless Adapter on Linux

USB ID **28de:2432** identifies the Valve Steam Frame Wireless Adapter tested here.
The Linux network driver is **rtw89_8852cu** (Realtek RTL8852CU), with rtw89 core/USB
modules and matching firmware. Steam is not required for the Linux network interface
or for a VRhotspot access point. Headset software, pairing and streaming protocols
are separate requirements.

## Diagnose before installing anything

```sh
python3 tools/diagnose_frame_dongle.py
lsusb -t
iw dev
iw phy PHY_NAME info
iw reg get
```

The read-only Python tool detects the exact USB ID even when no driver or network
interface exists. It reports the bound driver and current interface name without
serials, MAC addresses, SSIDs or credentials. It requires neither root nor Steam.
Do not assume a fixed interface name, that kernel version alone guarantees support,
or that an AP capability flag proves a working hotspot.

Prefer your distro's supported kernel/firmware packages with this USB ID enabled.
Valve's [Linux diagnostic](https://github.com/ValveSoftware/SteamVR-for-Linux/blob/master/frame-dongle-troubleshoot.sh)
identifies missing driver and regulatory configuration as common blockers.
The [upstream driver](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/realtek/rtw89/rtw8852cu.c)
contains the USB ID and newer board-specific behavior. Backports and custom kernel
configurations vary: the tested 7.1.13 custom source had the driver, but its running
configuration disabled RTW89. Exact matching external modules restored it without
replacing the kernel. Those module binaries are not portable across kernels/distros.

## Select the adapter

Once the driver creates the interface, VRhotspot's existing capability-based
inventory detects it. Select that USB interface explicitly as the AP adapter,
preserving the built-in adapter as the current uplink. No USB vendor-based tuning
or automatic transfer of an active connection is applied.

Use your actual country and channels permitted by the driver and regulatory DB.
On the US test machine, 5 GHz channel 36 at 80 MHz with Wi-Fi 6 was validated for
AP startup. 6 GHz frequencies remained `no IR` (no initiating radiation), so a
6 GHz PC hotspot was not attempted. No regulatory checks were bypassed. Despite
older driver capability output listing 2.4 GHz, that band was not qualified for
this product; newer upstream code has a device-specific 2.4 GHz restriction.

Valve's observed direct-link topology has the **headset hosting the 6 GHz AP** and
the USB adapter joining it as a client. This differs from VRhotspot's PC-hosted AP.
Supporting the latter is not proof of replacing Valve's pairing, multi-link or
streaming implementation. Do not claim SteamOS-equivalent or better throughput,
latency, sleep recovery, or 6 GHz behavior without measured comparative tests.

## Physical evidence and limits (2026-09-29)

- USB SuperSpeed 5000 Mb/s enumeration, exact matching driver and firmware loaded.
- Native ARM64 hostapd started a 5 GHz/80 MHz AP after NetworkManager released it.
- VRhotspot recommended the USB adapter; its own authenticated start and stop API
  both succeeded, with the built-in Wi-Fi and default route preserved.
- The first standalone hostapd attempt raced NetworkManager release and crashed
  during error cleanup. A release wait plus link-down resolved the startup test;
  VRhotspot's normal lifecycle subsequently succeeded. Retain this failure in
  compatibility evidence rather than treating all handoff paths as validated.
- Headset association/DHCP, sustained transfer, latency under load, USB replug,
  suspend/resume, direct-link pairing and other distributions remain to be tested.

Use a separate AP-capable adapter for this test rather than replacing the active
uplink. If USB removal or startup fails, release only the selected interface and
retain the user's uplink. Do not globally disable NetworkManager, rfkill or firewall.

## Wi-Fi 6 capability detection correction

A normal hostapd version banner lists neither HE nor SAE build options. Treating
missing words as `false` silently disabled Wi-Fi 6 even on the native HE-enabled
build. The probe now reports `unknown` for unlisted features and retains explicit
positive markers. Existing policy still handles genuine explicit negative evidence.
This does not assume support or bypass AP/channel checks: startup can still reject
an unsupported configuration. After the correction, the tested dongle successfully
started through VRhotspot with `--wifi6`, at 5 GHz/80 MHz. Client negotiation and
throughput remain separate tests.

## 6 GHz direct-link driver requirement

An older rtw89 USB driver may reject a US VLP headset AP during authentication with
`failed to insert STA entry for the AP (error -22)`. This error alone is not a unique
diagnosis. Check whether your distro backports upstream Linux
[bf4a37f516f0382832c10a9d04414944d0d96591](https://github.com/torvalds/linux/commit/bf4a37f516f0382832c10a9d04414944d0d96591),
which stops USB devices depending on internal Wi-Fi card ACPI capability checks.
Do not change country or bypass cfg80211 channel/power restrictions as a workaround.

A matching-kernel build with that unchanged patch physically connected the tested
Valve dongle to the Frame's WPA3 6 GHz/160 MHz AP, including reconnection to its
hidden SSID. Short TCP samples measured 1017.81 Mb/s to the Frame and 423.77 Mb/s
reverse. Loaded ping averaged 17.819/19.479 ms, zero loss in 90 probes each. These
results establish a working radio link, not Valve streaming parity or a complete
VRhotspot direct-mode product. The experimental profile was removed and the working
5 GHz hotspot restored. Credentials and module binaries are not shipped here.

`python3 tools/diagnose_frame_dongle.py --link` now reports sanitized current role,
frequency, channel width and PHY rates. It does not print peer MACs, SSIDs or keys,
and never treats radio rate as measured throughput. Patch presence is reported as
unknown: kernel version alone cannot establish it on distro backports.

On the tested Frame, disabling power save only for the VR-Hotspot saved connection
reduced idle mean ping from 51.353 ms to 3.026 ms (100 probes, no loss):
`sudo nmcli connection modify VR-Hotspot 802-11-wireless.powersave 2`, followed by
`sudo iw dev wlan0 set power_save off`. This can increase power use. Restore the
original default with profile value 0 and the prior live state with power_save on.
Do not apply these settings to unrelated connections or promise the same gains on
other hardware. Internet and Frame Control through the 5 GHz hotspot were verified.

## Experimental Frame Direct mode

The preceding sections record the earlier driver bring-up. VRhotspot now includes
an explicit **Frame Direct** section below the app header, in both Basic and Pro
views. This is a distinct operating mode: the Frame is the WPA3 AP, and the Valve
USB adapter is a 6 GHz client. It does not create a 6 GHz PC AP or require Steam on
that PC. The existing Frame AP service is still required on the headset.

Pair using the Frame direct-network SSID, BSSID and password (not the account or
home-Wi-Fi password). Select the Valve interface and choose **Connect Frame Direct**.
The saved NetworkManager profile uses SAE, required PMF, hidden-SSID discovery,
power saving disabled, no automatic connection, no default route and no imported
DNS/routes. Laptop and headset retain their existing independent internet uplinks.
**Internet relay over this direct link is not implemented**; use the ordinary
hotspot with internet sharing for that workflow.

The daemon accepts only a sysfs-verified USB 28de:2432 interface, refuses default
uplinks and unrelated active connections, and serializes role changes with the AP
lifecycle. It waits for NetworkManager readiness after AP shutdown, then verifies
association, DHCP, 6 GHz and 160 MHz. Connection failure restores a previously
running hotspot. **Disconnect** releases the direct link; **Return to Hotspot**
starts the saved ordinary hotspot configuration. These transitions leave the
laptop's uplink intact.

The root-only pairing file is
`/etc/NetworkManager/system-connections/vr-hotspot-frame-direct.nmconnection`
(UUID `c463edeb-f2ee-48f3-bf4c-99fc7600341f`). Pairing values are never passed in
command arguments, returned by status, or added to generic hotspot config exports.
The non-secret root-only session journal is
`/var/lib/vr-hotspot/frame-direct-session.json`. While this journal exists, ordinary
AP start/stop/repair is blocked, including daemon-startup repair. The direct link
can survive daemon restart; explicit Disconnect recovers an interrupted session.
Autoconnect is deliberately disabled, so a reboot/replug may require Disconnect
then Connect. Automatic suspend/replug recovery is not qualified.

Authenticated loopback API:

- `GET /v1/frame-direct`: sanitized pairing, adapter and link status.
- `POST /v1/frame-direct/pair`: `ssid`, `bssid`, `passphrase`.
- `POST /v1/frame-direct/connect`: `adapter`.
- `POST /v1/frame-direct/disconnect`: optional boolean `restore_hotspot`.

Do not put pairing secrets in shell arguments. Use the native form or an authenticated
client that keeps them in memory. There is no root-daemon SSH key access or automatic
credential extraction. A paired headset may need pairing again if its AP credentials
change. Frame Control discovery/route preference remains independent of this feature.

### Reference and product tests

SteamMini reference: SteamOS 3.9.2, x86_64 Valve kernel 7.2.7, NetworkManager with
IWD. Dongle was a managed client at 6135 MHz/160 MHz, with Ethernet default route
preserved. Firmware SHA-256 matched the laptop's firmware. No reference machine
settings were changed. At roughly -56 to -58 dBm, eight-second single-stream TCP
samples measured **427.75 Mb/s toward Frame / 643.35 Mb/s reverse**; idle ICMP
mean **3.131 ms**, maximum 5.601 ms, no loss in 100 probes.

Installed ARM64 product mode: WPA3/PMF, DHCP and 6135 MHz/160 MHz verified; daemon
restart preserved the link; Return to Hotspot restored channel 36/80 MHz; an
unavailable-network test restored that hotspot automatically. The handoff test also
exposed a NetworkManager readiness race, now addressed by a bounded readiness wait.
At a weaker approximately -68 dBm placement, product-mode TCP measured **393.91 /
349.85 Mb/s**, idle mean **2.777 ms**, maximum 5.578 ms, no loss in 50 probes.
Loaded ping means were 8.455/15.350 ms, maxima 41.749/44.247 ms, no loss in 90 probes
each. These are short TCP samples, not VR streaming certification, and unequal
placements prevent an overall Valve parity claim. PHY rate is not application
throughput. Sustained streaming, movement/obstruction, power cycles, sleep recovery,
other distributions and complete offline internet relay remain unqualified.

Sleep/wake observation: the direct AP and home-Wi-Fi SSH disappeared together;
the owner confirmed the Frame had gone to sleep. After wake, explicit Disconnect
then Connect succeeded at 6135 MHz/160 MHz with the saved pairing and the Frame's
home-Wi-Fi internet route intact. Automatic wake reconnection is not implemented.
An unavailable link keeps the session reserved until explicit Disconnect. This is
one manual wake/reconnect test, not sustained unattended-link validation. Visible
desktop-UI confirmation remains pending. Local suite: 1,872 passed, 5 skipped,
4 subtests passed; GitHub CI passed for the implementation commit.
