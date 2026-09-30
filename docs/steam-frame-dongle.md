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
