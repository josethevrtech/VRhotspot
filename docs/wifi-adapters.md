# Supported Wi-Fi Adapters

VR Hotspot needs a Wi-Fi adapter that supports AP (access point) mode. USB
adapters (`wlan1` and up) are strongly recommended over built-in adapters
(`wlan0`).

## ✅ Recommended (tested & working)

- **BrosTrend AXE3000 Tri-Band** (Best Choice) - https://www.amazon.com/dp/B0F6MY7H62
- **EDUP EP-AX1672** - https://www.amazon.com/EDUP-Wireless-802-11AX-Tri-Band-Compatible/dp/B0CVVWNSH2
- **Panda Wireless PAU0F AXE3000** - https://www.amazon.com/Panda-Wireless%C2%AE-PAU0F-AXE3000-Adapter/dp/B0D972VY9B

## ℹ️ Should work (untested)

- See compatible adapters list: https://github.com/morrownr/USB-WiFi/blob/main/home/USB_WiFi_Adapters_that_are_supported_with_Linux_in-kernel_drivers.md#axe3000---usb30---24-ghz-5-ghz-and-6-ghz-wifi-6e

## ⚠️ Known issues

- **(wlan0)**: Built-in adapters often have AP mode limitations. Use wlan1+
  (USB adapters) for better reliability.
- **Intel AX200 (wlan0)**: Known hardware limitation in AP mode; use a USB
  adapter instead. See `../BUNDLED_LIBNL_SETUP.md`.

For how VR Hotspot detects, scores, and recommends adapters, see
`adapter-intelligence-v2.md` and `architecture.md`.
