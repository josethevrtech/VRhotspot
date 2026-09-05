# Streaming stability milestone

Status: work in progress. The changes on this branch fix demonstrated software
defects. Subscriber latency and Flatpak reports still require reproduction and
verification on the affected machine before they can be closed.

## What the reports establish

A public subscriber reports intermittent Quest freezes using a recommended
dongle with both SteamVR and WiVRn. Another report identifies CachyOS and an EDUP
adapter, and says the installer appears to complete without a usable Flatpak
tray application. We do not yet have the exact EDUP model/USB revision, Quest
model, kernel, firmware, stream settings, or incident logs.

The two streamers share the radio, USB connection, rendering host, encoder, and
headset. A failure in both increases the value of testing those shared parts;
it does not prove that the dongle is defective. SteamVR is a runtime, so also
record the actual wireless transport/client used with it.

## Fixes in this branch

- The watchdog recovers missing/exited processes but no longer restarts a
  healthy AP because of a low RF quality score. Poor quality is an advisory.
  Old `auto_channel_switch=true` settings cannot trigger scans or a stop/start
  cycle during streaming. Startup channel selection remains available.
- The watchdog no longer changes TX power from AP-side RSSI or saves an
  automatically calculated value as a manual preference. AP-side RSSI describes
  reception from the headset; it is not a measurement of headset downlink margin.
- Manual TX power is converted correctly from dBm to iw's mBm. For example,
  20 dBm must be sent as 2000, not 20. `null` means driver-managed auto.
- Power is applied once during successful startup across lnxrouter, NAT, bridge,
  and 6 GHz paths, only after verifying a dedicated AP radio. An unrelated,
  shared, or unverified radio is left unchanged, with a diagnostic warning.
  Requested and driver-reported values remain separate in `tuning.tx_power`;
  driver failures become warnings.
- Unsupported `tx_power=` hostapd lines are removed. Invalid manual values are
  rejected before configuration persistence or startup rather than truncated.
- Companion install verification, preserved install logs, and a companion-only
  repair path address false success and accidental companion removal on update.
- Installer cleanup preserves user-supplied source checkouts, including those in
  `/tmp`. Only a temporary clone created and owned by that installer run is removed.
- The existing channel selector now considers legal complete channel blocks,
  including idle candidates and overlapping networks. It refuses to scan an
  active or unverified streaming radio. Automatic selection is a startup-only
  decision and does not overwrite the saved manual channel. Shared channel
  geometry keeps the probe and hostapd/linux-router width settings consistent;
  see [channel selection](channel-selection.md).
- The existing UDP echo diagnostic now independently paces sends and receives,
  verifies the peer/session/payload, uses monotonic time, and reports delayed,
  reordered, duplicate, and burst-loss evidence. Tail percentiles are withheld
  when there are too few replies. It is an optional active echo test, not an
  automatic streaming benchmark; see [UDP diagnostics](udp-diagnostics.md).
- A bounded passive streaming timeline is available through the existing UI,
  authenticated API, Flatpak companion, and [CLI](cli.md). It records radio
  settings, station counters, queue backlog, adapter/USB facts, classified host
  kernel events, and timestamped freeze markers. The existing support ZIP
  includes the latest retained timeline. No scan, synthetic traffic, power
  adjustment, or AP restart is performed by the recorder.
- The existing telemetry cache uses monotonic intervals, serializes concurrent
  sampling, isolates callers' snapshots, and resets stale client/radio baselines.
  Driver TX failures are no longer presented as receiver packet loss.

The units are specified by the [Linux Wireless iw documentation](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html#setting-tx-power).
The faulty conversion could request approximately 0.20 dBm when the UI intended
20 dBm. Driver behavior varies, so this is a confirmed code defect and a plausible
contributor, not proof of the subscriber's root cause.

Existing saved `tx_power` values cannot be reliably distinguished from deliberate
manual choices. This branch does not silently rewrite them. During an agreed
support test, choose Auto and restart the hotspot once outside the VR session,
then verify the requested/reported power and any driver warnings.

Some drivers apply a power request to the whole physical radio, even when the
command names an interface; see the kernel's [cfg80211 TX-power interface](https://cdn.kernel.org/doc/html/latest/driver-api/80211/cfg80211.html).
The dedicated-radio check is deliberately conservative. If it skips a request,
do not assume a saved value was applied. A fixed value can persist after stopping
the hotspot on a reused radio. Generic `iw` readback does not reveal its previous
auto/fixed policy, so this branch does not guess a rollback value. Choose Auto
and start once on the verified dedicated radio outside a VR session to reset it.
Full prior-policy restoration remains a follow-up; do not claim it is implemented.

For the missing desktop app, use the [companion-only recovery guide](flatpak-companion.md#repairing-a-missing-desktop-app).
The candidate verifies the registered user install, desktop launcher, offline
entry point, and graphical library imports. A real desktop session still needs
to confirm the window, authentication, and tray icon. Do not tell the subscriber
to rerun the full daemon installer just to repair the companion.

## First subscriber reproduction

1. Collect the exact EDUP model, `lsusb` ID, `lsusb -t` negotiated USB speed,
   `uname -r`, driver/firmware version, Quest model/system version, app commit,
   streamer/client version, codec, bitrate, refresh rate, and render resolution.
   Include whether rendering happens on the hotspot host or another PC.
2. Start the passive streaming recorder in Pro mode before reproducing the
   problem, and click **Mark freeze** when it happens (or use the CLI from a
   second terminal). Stop the recording and download the existing sanitized
   support bundle promptly; it includes the latest retained timeline. A separate
   JSON export is also available. Record the time and timezone, and inspect
   exported artifacts before sharing them publicly.
3. Record a short repeatable scene under unchanged game/codec/resolution/refresh
   settings. Correlate freezes with WiVRn render/encode/network/decode timings,
   station retry/failure deltas, actual channel/width, TX power, and kernel USB/
   firmware/reset events. Do not conclude that an average ping or PHY rate proves
   streaming stability.
4. Compare WiVRn over the EDUP hotspot, over USB, and over an Ethernet-connected
   dedicated AP using the same host/headset/settings. For the USB control,
   follow WiVRn's USB instructions and disable headset Wi-Fi to verify that the
   session really uses the cable.
5. Repeat the failing wireless case with the candidate build and Auto TX power.
   Use a legal, clear, non-DFS 5 GHz 80 MHz channel as the initial baseline, a
   USB 3 port with adequate power, line of sight, and one headset on the AP.
   Do not change channels while the session runs.
6. Start at a conservative fixed video bitrate, for example 80–100 Mbps if the
   chosen codec/streamer supports it, then increase one step at a time. The exact
   supported bitrate becomes part of the qualification result. If necessary,
   test 40 MHz as a separate interference experiment, with compatible bitrate.
7. Only after the above, A/B-test device-scoped USB autosuspend or power-save
   changes, keeping prior values for restoration. Avoid enabling every tuning
   toggle at once; IRQ affinity, coalescing, global buffer sizes, and aggressive
   QoS need evidence that they improve the measured failure.

WiVRn's [troubleshooting](https://github.com/WiVRn/WiVRn/blob/master/README.md)
and [debugging guide](https://github.com/WiVRn/WiVRn/blob/master/docs/debugging.md)
cover bitrate/resolution, timing capture, logs, and USB comparisons. USB power
experiments should follow the kernel's [per-device power-management interface](https://docs.kernel.org/driver-api/usb/power-management.html).

Interpret the control tests as follows:

| Observation | Next investigation |
|---|---|
| USB also freezes with matching render/encode spikes | Game/render workload, encoder scheduling, GPU/CPU saturation or thermal limits |
| USB and wired-AP Wi-Fi are smooth; dongle hotspot freezes | VRhotspot settings, USB topology/power, driver/firmware, radio interference |
| Both wireless routes freeze; USB is smooth | RF environment, headset Wi-Fi, bitrate/decoder limits, wireless stream pacing |
| Freeze coincides with watchdog restart | Verify candidate fix and ensure the installed code is the candidate, not only the checkout |
| TX rate/retries collapse or USB resets occur | Driver/firmware/USB evidence and hardware qualification |
| Network timings remain stable but decode spikes | Headset/codec/resolution/thermal behavior |

If the game runs on the hotspot host, video sent to the local AP interface does
not traverse the Internet NAT forwarding path. NAT acceleration and TCP BBR are
therefore poor first guesses for a local UDP streaming stall. Verify the actual
route before choosing a tuning experiment.

## Qualification criteria

These are proposed project acceptance targets, not standards or a promise that
all radio environments can achieve them. Every result must name its exact
hardware, software versions, topology, codec, bitrate, resolution, refresh rate,
channel/width, and test conditions.

For the initial 90 Hz reference configuration:

- Three repeatable two-hour sessions after thermal warm-up, with normal head
  movement and the same demanding scene sequence.
- Zero unexpected AP restart, channel switch, disconnect, USB/firmware reset,
  or user-visible freeze attributable to VRhotspot/the wireless path.
- Receiver-measured packet loss under 0.1%, with loss bursts and their duration
  reported separately; aggregate loss must not conceal a bad burst.
- Under the qualified video load, an initial local RTT budget of p99 <= 10 ms,
  p99.9 <= 20 ms, and no unexplained >50 ms events. RTT is only a network proxy;
  it is neither one-way delay nor end-to-end motion-to-photon latency.
- Separately capture late/dropped video frames and render/encode/decode times.
  A candidate passes only if its network behavior and the visible experience
  improve together. Compare frame deadlines to the selected refresh rate
  (11.11 ms per frame at 90 Hz), not to a universal latency number.
- Repeat after reboot, five headset sleep/wake/reconnect cycles, and USB
  unplug/replug recovery outside an active VR session.
- Confirm behavior with a modest concurrent download and repeat in a second
  RF environment. Record the usable bitrate ceiling rather than claiming the
  marketing link speed is application throughput.

Any unqualified or unmeasured case remains unqualified even if the unit suite
passes. Replace broad "Best Choice" claims with compatibility and published
VR qualification results.

## Remaining release gates

1. Ship this branch as a test candidate after automated review. Resolve the
   subscriber's Flatpak launch/install case and collect before/after streaming
   traces. Keep the issue open until the subscriber reproducer passes.
2. Use the implemented passive recorder and streamer timing export together.
   Two-second station/queue samples can miss a short stall; they cannot measure
   headset receive loss, network p99, encoding/decoding, or motion-to-photon
   latency. Classified USB/GPU/firmware events are host-wide clues, not proof
   that a particular adapter caused the freeze.
3. The repaired UDP diagnostic still requires an existing trusted byte-echo
   endpoint. A headset-side receiver and frame-burst/load qualification harness
   remain future work. Do not add an unauthenticated listening service or claim
   that an idle echo result certifies a loaded video stream.
4. Validate startup selection and actual channel/width on each reference driver.
   Scan-based interference estimates are not utilization measurements. Survey
   busy-time needs a safe, time-bounded baseline before it can inform scoring;
   cumulative counters alone must not be marketed as live utilization.
5. Qualify exact dongle models/revisions and a dedicated wired AP control. Keep
   firmware/kernel version results so rolling-release regressions are visible.

## Subscriber reply draft

Thanks for reporting this. I found software issues in VRhotspot that could cause
stream interruptions, including automatic hotspot restarts on brief quality
drops and incorrect transmit-power units. I am testing fixes, and I am also
fixing the installer so it verifies the Flatpak app and keeps useful failure
logs. I do not want to call your EDUP adapter faulty without measuring it.

Please send the exact EDUP and Quest models, your kernel version, streamer
settings, and a sanitized support bundle captured just after a freeze, with the
time it occurred. For the tray issue, the output of `flatpak list --user --app`
and `flatpak info --user io.github.josethevrtech.VRhotspot` will tell us whether
the app is missing or installed but failing to launch. I will use this case as
a release acceptance test and confirm the result with you on the test build.
