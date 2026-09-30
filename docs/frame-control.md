# Frame Control integration

The desktop companion tray offers **Control Steam Frame** when the optional
MainFrameOS Frame Control user service is running. While connected the action
becomes **Return Control to Laptop**; while connecting it can cancel the attempt.
The existing F11 shortcut (Fn+F11 on the tested laptop) and Ctrl+Alt+Escape fallback
continue to work. No hotspot needs to be running to use this on an existing LAN.

Install the user service separately using the
[MainFrameOS implementation](https://github.com/josethevrtech/MainFrameOS/pull/21).
Configure its paired headset and receiver there. VRhotspot does not install the
receiver, acquire uinput permissions, pair SSH keys or collect input events.

## Process boundaries

The GTK tray calls only `Status` and `Toggle` on the session D-Bus service
`org.mainframeos.FrameControl`, object `/Control`. Calls have a 500 ms timeout and
do not auto-start an absent service. The Flatpak adds only the corresponding
`--talk-name` permission. Missing services or unexpected states disable the action.

The user service owns input capture and strict-key SSH transport. The root hotspot
daemon never handles SSH credentials or input capture. The small active status
strip is still a focused input surface, not a windowless compositor capture API.

## Portable/offline use

VRhotspot already provides a local hotspot and DHCP. Disable **Share Internet
Connection** for an offline network. Connect the Frame to that network, then use
Frame Control. Internet access is not required by its SSH transport.

For changing network addresses, the companion service supports a paired `.local`
hostname through Avahi. It resolves the address but keeps SSH verification pinned
to the original hostname. Pair and verify the key before travel; discovery alone
is not authorization. Multicast discovery must work on the chosen network.

The current vendor `hostapd` and `dnsmasq` executables inspected for this change
are x86-64. The ARM64 laptop needs compatible native networking binaries before
this hotspot path can be qualified. Use a supported AP-capable adapter; concurrent
upstream Wi-Fi and hotspot operation is hardware/driver dependent. This change
has not activated or validated an offline hotspot on the ARM64 laptop.

## Validation

The full Python suite passed: 1858 tests, 5 skipped, 4 subtests. Ruff passed.
A native Gio bridge test reached the actual headset through the paired hostname,
reported `controlling-frame`, and returned to `idle`. The owner previously verified
pointer, typing, clicks, and both Fn+F11 toggles. A packaged Flatpak UI build and
an end-to-end offline hotspot test remain outstanding.
