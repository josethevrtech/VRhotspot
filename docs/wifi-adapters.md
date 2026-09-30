# Wi-Fi Adapter Compatibility and VR Qualification

For ordinary internet sharing, VR Hotspot needs an adapter whose Linux driver
supports AP (access point) mode on the intended band and channel. A paired-headset
connection instead requires compatible managed/client mode. A working hotspot does not establish sustained
VR streaming quality. Kernel, firmware, USB connection, regulatory restrictions,
radio conditions, and headset settings all matter. Interface names such as
`wlan0` and `wlan1` do not identify built-in versus USB hardware or its reliability.

## What to choose (updated 2026-09-30)

Choose for the connection you need. A 6 GHz **client** can join a headset-hosted
network even when the PC cannot legally or technically host a 6 GHz access point.
This is the approach validated with Steam Frame; it is not an unlock for arbitrary
radios. VR Hotspot never overrides regulatory restrictions or hardware limits.

| Device / family | Recommendation and evidence | Remaining validation |
| --- | --- | --- |
| **Steam Frame USB**, `28de:2432`, RTL8852CU | Current tested choice for the Frame-hosted link: WPA3/PMF, DHCP, **6135 MHz / 160 MHz**, disconnect and rollback verified on MainFrameOS ARM64. Also tested as a 5 GHz / 80 MHz PC hotspot. | Sustained VR streaming, equal-condition Valve performance comparison, other distributions and wake/replug automation. |
| **Other RTL8852CU USB adapters** with an upstream-supported USB ID and working `rtw89_8852cu` driver | Candidates for the same headset connection. The same runtime capability checks and connection safeguards now apply, without a Valve brand gate. | No non-Valve unit physically tested in this bring-up. Verify exact USB ID, kernel, firmware and negotiated link before purchasing for this purpose. |
| **MediaTek MT7921AU/MT7921U USB family**, including EDUP EP-AX1672 as identified by its manufacturer | Candidates for headset connections through `mt7921u` when the actual adapter reports 6 GHz and HE160 in managed mode. Existing AP reports are listed below. | Frame interoperability and 160 MHz negotiation have not been physically tested here. Retail revisions may differ. |
| USB adapters limited to Wi-Fi 5 or 2.4/5 GHz | Continue to use the ordinary hotspot workflow at supported settings. | They cannot gain a 6 GHz radio through software. |

Kernel sources list the supported USB IDs for
[RTL8852CU](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/realtek/rtw89/rtw8852cu.c)
and [MT7921U](https://github.com/torvalds/linux/blob/master/drivers/net/wireless/mediatek/mt76/mt7921/usb.c).
Examples include `0bda:c832` and `0e8d:7961`; these are chipset/driver identifiers,
not proof of a particular retail model or VR performance. Check the source for
your installed kernel, not just upstream master. The driver's reported capabilities
are authoritative for offering the experimental connection, and actual association,
DHCP, 6 GHz and 160 MHz negotiation must all pass before success is reported.

The app displays **Steam Frame USB** for its exact USB ID. Other USB adapters use
the product name supplied by the device, falling back to a numbered Wi-Fi label
when unavailable. Identical product names are distinguished by interface. Controls
follow connection role, known band support, bus type and enabled features in both
Basic and Pro. A 6 GHz hotspot option additionally needs HE AP support in that
band and at least one frequency without disabled/NO-IR flags. Client support alone
does not expose it. Exact channel/width legality is still checked at startup;
unknown capabilities still require runtime validation. Saved hotspot
preferences remain separate from headset pairing.

## Existing project compatibility reports

These models were previously listed as tested and working. Retain them as
compatibility candidates; sustained streaming qualification is pending for each.
No model below is currently ranked as the best choice or guaranteed stutter-free.

| Model | Current evidence | VR streaming qualification |
| --- | --- | --- |
| BrosTrend AXE3000 Tri-Band | Existing project working-AP report | Pending |
| EDUP EP-AX1672 | Existing project working-AP report; manufacturer specifies MTK7921AU, USB 3.0, and 2T2R | Pending |
| Panda Wireless PAU0F AXE3000 | Existing project working-AP report | Pending |

The [EP-AX1672 manufacturer specifications](https://www.szedup.com/usb-adaptersBr3/EP1672.html?act=specification)
identify that exact model. The [product overview](https://www.szedup.com/usb-adaptersBr3/EP1672.html)
advertises AP mode but lists Windows compatibility; it does not certify Linux VR
latency. Linux's [MediaTek driver documentation](https://wireless.docs.kernel.org/en/latest/en/users/drivers/mediatek.html)
records MT7921 USB support. Neither source proves the performance of every EDUP
adapter. The reviewer reporting CachyOS stutters said only “EDUP”; their exact
model, USB ID, driver, and firmware still need to be confirmed.

## Other candidates and limitations

- The community [Linux USB Wi-Fi compatibility list](https://github.com/morrownr/USB-WiFi/blob/main/home/USB_WiFi_Adapters_that_are_supported_with_Linux_in-kernel_drivers.md#axe3000---usb30---24-ghz-5-ghz-and-6-ghz-wifi-6e)
  is a starting point for research, not VR Hotspot streaming qualification.
- Existing project notes flag **Intel AX200** AP-mode limitations. Check the
  actual driver, supported interface modes, and permitted frequencies before
  choosing it for a hotspot; see [bundled libnl notes](../BUNDLED_LIBNL_SETUP.md).
- A Wi-Fi 6E label does not by itself establish usable 6 GHz AP mode on Linux.
  Inspect the adapter's capabilities and regulatory restrictions with `iw list`;
  see the [official iw documentation](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html).

## Before replacing hardware

Follow the [streaming stability guide](streaming-stability.md) to compare the
hotspot with WiVRn over USB and with the PC connected by Ethernet to a dedicated
access point. Keep the game, bitrate, resolution, and refresh rate consistent.
This comparison helps identify whether the Wi-Fi path contributes to the stutter.
[WiVRn's troubleshooting guidance](https://github.com/WiVRn/WiVRn/blob/master/README.md)
includes lowering bitrate/resolution and testing USB or a better router. Use an
available access point for the comparison before buying an unverified model.

Publish the exact hardware and software versions, settings, session duration,
latency distribution, loss, and observed interruptions before promoting a model
to VR-validated status. Adapter scores alone are not a streaming benchmark; see
[adapter intelligence](adapter-intelligence-v2.md) and [architecture](architecture.md).

## Steam Frame Wireless Adapter

See [Steam Frame dongle support](steam-frame-dongle.md) for USB ID `28de:2432`,
Steam-independent Linux driver checks, tested hotspot and headset connections,
regulatory limits and remaining performance qualification. Detection is not a performance claim.
