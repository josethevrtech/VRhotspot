# VR Hotspot command-line diagnostics

The CLI talks to the existing daemon API. It never directly operates a radio,
restarts a hotspot, installs tools, or changes power settings. The streaming
commands control only an in-memory passive evidence capture.

## Authentication and output

The client reads `/etc/vr-hotspot/env` when your account can read it, with
`VR_HOTSPOTD_API_TOKEN` and `VR_HOTSPOTD_API_URL` environment overrides. Use
`--token-stdin` to enter the token without placing it in shell history or process
arguments. Avoid `--token` for routine use. Redirects are rejected before an
authentication header can be forwarded.

Commands print JSON to standard output by default. `--output PATH` creates a new
owner-only file (mode `0600`), refusing existing files and symlinks. Choose a new
filename for each export. `--api-url`, `--env-file`, `--timeout`, `--token-stdin`,
and `--output` belong after the final subcommand.

```sh
vr-hotspot status
vr-hotspot preflight --output preflight-before.json
vr-hotspot diagnostics streaming capture --duration 120 --output vr-session.json
```

## Capture a freeze without disturbing the stream

Start a two-minute capture, then reproduce the problem in your normal VR app.
The foreground command waits and exports the resulting JSON. Pressing Ctrl-C
stops that specific capture and exports its partial evidence; the command exits
with status `130` to indicate interruption. The initial progress message prints
the capture ID; keep it so you can recover the report if the daemon becomes
unreachable before export.

Use detached mode when working from another terminal or collecting a freeze
marker while the game is running:

```sh
vr-hotspot diagnostics streaming capture --duration 120 --detach
vr-hotspot diagnostics streaming status
vr-hotspot diagnostics streaming mark --capture-id CAPTURE_UUID
vr-hotspot diagnostics streaming stop --capture-id CAPTURE_UUID
vr-hotspot diagnostics streaming report --capture-id CAPTURE_UUID --output vr-session.json
```

Replace `CAPTURE_UUID` with the exact ID returned by `capture` or `status`. Each
`mark` adds a fixed "freeze" marker with a timestamp; no private free-text notes
are accepted. `stop` retains the report for export. `report` also works while a
capture is running, producing a partial snapshot. Only one capture runs at a
time, and commands cannot accidentally mark or stop a replacement session.

Captures last 10–600 seconds, sample at most every two seconds, and retain at
most 300 samples and 256 freeze markers. Slow collectors skip missed sampling
slots instead of creating catch-up bursts. The sanitized report is bounded below
the companion's existing transport limit. Missing or oversized telemetry is
explicitly identified; it is not evidence of a healthy network.

Only the latest capture is retained, in daemon memory. A new capture replaces a
completed report, daemon shutdown loses it, and access expires 30 minutes after
completion. Export promptly. A capture makes no active scans, speed tests, ping
traffic, channel switches, or power adjustments.

## How this differs from the existing support bundle

The existing support bundle remains the source for current installation,
configuration, readiness, and inventory facts. Existing lifecycle capture logs
remain the source for engine startup and failure logs. Neither records a
sequence of passive streaming measurements correlated with user freeze markers.
The streaming capture adds that missing timeline; it does not create another
support-bundle system or replace the existing diagnostic endpoints.

The Pro UI's connection-quality section exposes the same Start, Mark freeze,
Stop, and Download actions. It uses the existing status refresh, not another
polling loop. The existing support ZIP automatically includes the latest
retained timeline, even if it is still running; exporting it does not start a
new recording. Use the separate JSON export if you only need that timeline.

Provide the sanitized support bundle after recording a freeze. Also include
the exact adapter model/USB ID, Quest model, VR app,
codec, bitrate, refresh rate, and the approximate time the freeze happened.
Review exported files before sharing them publicly.

Passive radio evidence cannot measure encoder time, headset decoder time, or
one-way network latency. Compare the capture with the VR app's timing overlay
or its supported timing export. See [streaming stability](streaming-stability.md)
for the reproduction and qualification procedure.
