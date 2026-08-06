# Developer Hub and ADB Dev Bridge

Developer Hub provides a typed, authenticated Linux workflow for standalone XR
headsets. It discovers ADB devices, guides USB authorization, establishes a
wireless development transport over VRhotspot, deploys APK files, and exposes
allowlisted application actions.

The dedicated VRhotspot network is the trust and transport boundary for guided
wireless ADB. Developer Hub must not enable wireless ADB through an arbitrary
home, office, public, or third-party hotspot network.

## VRhotspot-only wireless policy

Guided wireless setup may execute `adb tcpip` and `adb connect` only after the
daemon verifies all of the following:

1. VRhotspot is running.
2. The active hotspot configuration is complete.
3. The headset is connected to the active VRhotspot SSID, or the daemon has
   explicitly requested that exact SSID through the typed Android Wi-Fi shell
   operation.
4. The headset IPv4 address belongs to the active VRhotspot subnet.
5. The headset default route uses `wlan0` and is consistent with the active
   VRhotspot gateway.

The sequence fails closed. A missing or mismatched SSID, an address outside the
hotspot subnet, a stale route, or a gateway mismatch prevents both `adb tcpip`
and `adb connect`.

## Guided headset setup

When the hotspot is stopped, Developer Hub first presents **Start VRhotspot
first**. Starting delegates to the existing hotspot lifecycle and validation
workflow; Developer Hub does not own a second start implementation.

After the hotspot is confirmed running, the wizard uses five stages:

1. Connect USB
2. Approve debugging
3. Join VRhotspot
4. Enable wireless
5. Complete

The browser submits only the selected USB ADB serial and the fixed wireless ADB
port. It never receives or submits the hotspot passphrase.

The daemon reads the active SSID, security mode, passphrase, AP interface,
gateway, and subnet from its authoritative configuration and runtime state.
Those credentials are not included in API results, logs, exceptions, support
bundles, or browser state.

## Wi-Fi capability and fallback

The daemon inspects the headset through fixed, allowlisted ADB argument arrays
with `shell=False`:

- USB authorization state
- product model
- `wlan0` IPv4 state
- default route
- Android Wi-Fi status
- Android Wi-Fi command capabilities

When supported, Developer Hub requests the exact active VRhotspot SSID through
Android's typed Wi-Fi shell command and waits for network verification before
enabling wireless ADB.

Horizon OS versions may restrict that operation. In that case, Developer Hub
asks the user to select the displayed VRhotspot SSID inside the headset. It
does not ask for an IP address and does not enable wireless ADB while waiting.
The USB connection remains the control channel until verification succeeds.

## Developer Tools

`GET /v1/devbridge/tools/status` reports the effective ADB source and the
managed installation state.

Developer Hub presents management controls according to ownership:

- **Missing ADB:** Install Managed ADB
- **System ADB:** System ADB ready; no install or removal controls
- **Verified managed ADB:** Reinstall Managed ADB and Remove Managed ADB
- **Invalid managed installation:** Repair Managed ADB and Remove Managed ADB

VRhotspot never removes a system-owned ADB executable.

## Device and application workflow

Healthy ADB state is presented as connected context rather than the raw ADB
term `device`. Exceptional states use user-facing language such as **Approve
USB debugging**, **Offline**, and **Recovery mode**.

The normal application workflow uses:

- a browser file chooser
- drag and drop where supported
- the currently selected headset
- optional controlled install settings
- Install or update app

Daemon-host filesystem paths and editable target serials are not part of the
normal Developer Hub interface. The typed install-by-path backend operation is
retained for CLI and automation compatibility.

The desktop companion must use a safe native file-portal handoff before it can
upload APK bytes. Until that transport is available, it reports that deployment
is unavailable in the companion and directs the user to the authenticated Web
Portal; it does not expose a daemon path field.

## Typed API operations

All routes require the same authentication as other `/v1` endpoints.

Read operations include:

- `GET /v1/devbridge/status`
- `GET /v1/devbridge/devices`
- `GET /v1/devbridge/readiness`
- `GET /v1/devbridge/tools/status`
- `GET /v1/devbridge/adb/version`
- `GET /v1/devbridge/adb/devices`
- `GET /v1/devbridge/adb/packages`
- `GET /v1/devbridge/adb/device-overview`

Typed, user-triggered operations include:

- `POST /v1/devbridge/adb/enable-wireless`
- `POST /v1/devbridge/adb/install-upload`
- `POST /v1/devbridge/adb/install`
- `POST /v1/devbridge/adb/launch`
- `POST /v1/devbridge/adb/stop`
- `POST /v1/devbridge/adb/clear-data`
- `POST /v1/devbridge/adb/uninstall`
- `POST /v1/devbridge/adb/disconnect`
- `POST /v1/devbridge/tools/install`
- `POST /v1/devbridge/tools/remove`

The lower-level typed pair/connect operations remain available for CLI,
automation, tests, and internal guided workflows. Developer Hub does not expose
permanent manual pairing-code, IP-address, or ADB-port forms.

## Safety constraints

- No arbitrary ADB command surface.
- No client-supplied shell fragments or arbitrary ADB arguments.
- No `shell=True` subprocesses.
- No passphrase in public results or diagnostic artifacts.
- No wireless ADB mutation before VRhotspot network verification.
- Bounded polling, connection retries, output size, and subprocess timeouts.
- No automatic logcat collection.

## CLI foundation

The existing read-oriented CLI surfaces remain available:

```sh
vr-hotspot devbridge status
vr-hotspot devbridge scan
vr-hotspot devbridge scan --no-probe
vr-hotspot devbridge adb-command --ip 192.168.68.23
vr-hotspot devbridge logcat-command --ip 192.168.68.23
vr-hotspot devbridge tools status
```

They share the daemon's typed models and authentication contract. Developer Hub
does not replace Quest Link, SteamVR, ALVR, Virtual Desktop, or any VR runtime.


### Removing ADB from Development Tools

Development Tools now follows the detected ADB source. VRhotspot-managed ADB can be
reinstalled or removed. A system ADB installation exposes an **Uninstall System
ADB** action with an explicit confirmation; the daemon verifies that the detected
`adb` executable is owned by a known Android-tools package before invoking a fixed,
non-shell package-manager command. Immutable SteamOS and Bazzite hosts refuse system
package removal. When no ADB is detected, the UI offers the reviewed managed ADB
installation.

