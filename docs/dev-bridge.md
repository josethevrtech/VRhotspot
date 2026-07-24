# ADB Dev Bridge (read-only foundation)

The ADB Dev Bridge helps standalone VR headset developers (Unity Build & Run,
`adb`, logcat) work over the dedicated VRhotspot network. This document covers
the read-only foundation shipped first: discovery, reachability, copyable
commands, and readiness checks. Web Portal and Flatpak UI surfaces build on
these same contracts later.

## Principles

- **Read-only.** The daemon never executes `adb`, never mutates headset or
  host developer settings, and never collects logcat.
- **Copyable commands only.** Every actionable step is emitted as an explicit
  command string for the user to run in their own terminal.
- **No secrets.** Pairing codes are placeholders (`<PAIRING_CODE>`); they are
  shown only on the headset and are never printed, stored, or inferred.
- **One optional network interaction.** A plain TCP `connect()` probe against
  ADB port 5555 reports reachability. No ADB protocol bytes are exchanged, and
  the probe can be disabled with `?probe=0` (API) or `--no-probe` (CLI).

## API surfaces

All endpoints require the same API token as other `/v1` routes.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/devbridge/status` | Hotspot/subnet state, host `adb` presence, detected device counts. Probe-free. |
| `GET /v1/devbridge/devices` | Devices on the hotspot network with hostname-based headset classification and optional port-5555 reachability (`?probe=0` to skip). |
| `GET /v1/devbridge/adb` | Copyable `adb connect`, Wireless Debugging pairing, and logcat commands. Filters: `?ip=<IPv4>`, `?kind=connect\|logcat\|all`. Never probes, never executes. |
| `GET /v1/devbridge/readiness` | Ordered readiness checks for Unity Build & Run; every non-passing check carries `next_step` text and a copyable `next_step_command`. |

All payloads carry `schema_version` and are built by pure model builders in
`backend/vr_hotspotd/diagnostics/devbridge.py`, so the CLI, HTTP API, support
bundle, and future UI callers share one contract.

## CLI surfaces

The `vr-hotspot` CLI stays a read-only HTTP client of the daemon API and
prints the endpoint's JSON data:

```sh
vr-hotspot devbridge status            # GET /v1/devbridge/status
vr-hotspot devbridge scan              # GET /v1/devbridge/devices
vr-hotspot devbridge scan --no-probe   # skip the port-5555 TCP check
vr-hotspot devbridge adb-command --ip 192.168.68.23     # connect/pairing commands
vr-hotspot devbridge logcat-command --ip 192.168.68.23  # logcat helper commands
```

The shared connection flags from `vr-hotspot preflight` apply
(`--api-url`, `--token`/`--token-stdin`, `--env-file`, `--output`,
`--timeout`).

## Readiness checks

`GET /v1/devbridge/readiness` evaluates, in order: `hotspot_running`,
`ap_interface_present`, `network_config_valid`, `host_adb_present`,
`devices_detected`, `adb_tcp_reachable`, and `unity_build_and_run`. Statuses
are `pass`, `warning`, `fail`, `skipped`, or `unknown`; `overall` is `ready`,
`partial`, or `not_ready`. Checks that depend on the hotspot being up are
`skipped` (not failed) while it is down, and each carries a copyable next
step.

## Support bundle

`vr-hotspot/devbridge.json` in the support bundle contains the probe-free Dev
Bridge status. It passes through the standard support-bundle redaction (MAC
addresses and public IPs are redacted) and never contains logcat output or
pairing data.

## Non-goals

Dev Bridge does not replace Quest Link, SteamVR, ALVR, Virtual Desktop, or any
PCVR streaming stack, and it is not a VR runtime. Mutating operations, if ever
added, must be explicit and user-triggered.
