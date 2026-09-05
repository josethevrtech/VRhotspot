# Wi-Fi Adapter Compatibility and VR Qualification

VR Hotspot needs an adapter whose Linux driver supports AP (access point) mode
on the intended band and channel. A working hotspot does not establish sustained
VR streaming quality. Kernel, firmware, USB connection, regulatory restrictions,
radio conditions, and headset settings all matter. Interface names such as
`wlan0` and `wlan1` do not identify built-in versus USB hardware or its reliability.

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
