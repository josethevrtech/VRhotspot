# Building hostapd for 5 GHz 80 MHz (VHT)

VR Hotspot uses hostapd VHT keys like `ieee80211ac`, `vht_oper_chwidth`, and
`vht_oper_centr_freq_seg0_idx`. For 5 GHz 80 MHz support, make sure your
hostapd `.config` includes:

- `CONFIG_DRIVER_NL80211=y`
- `CONFIG_IEEE80211N=y`
- `CONFIG_IEEE80211AC=y`

If you build against libnl, enable the matching libnl option:

- `CONFIG_LIBNL32=y` for libnl >= 3.2
- `CONFIG_LIBNL20=y` for libnl 2.x

## Verify

Create a minimal VHT config and run `hostapd -t`. The output should not
contain "unknown configuration item".

```sh
cat > /tmp/hostapd-vht.conf <<'EOF'
interface=wlan0
driver=nl80211
ssid=vrhs-probe
hw_mode=a
channel=36
ieee80211n=1
secondary_channel=1
ieee80211ac=1
vht_oper_chwidth=1
vht_oper_centr_freq_seg0_idx=42
EOF

hostapd -t /tmp/hostapd-vht.conf 2>&1
```
