# Diagnostics Support Bundle Design

Status: implementation-backed design for unreleased VR Hotspot v1.1.0 work.
The current branch includes a limited authenticated web endpoint and Pro UI
download action. Full system collectors and a CLI helper remain future work.
This document does not mark v1.1.0 as released.

## Goals

- Give users a safe diagnostic artifact they can attach to GitHub issues.
- Capture enough platform, service, adapter, readiness, log, firewall, and
  configuration context to make support requests actionable.
- Redact secrets and personal identifiers before anything is written to the
  final bundle.
- Preserve command failures and missing-tool states so maintainers can
  distinguish "not installed" from "collected and empty".
- Keep the design compatible with both future web UI export and future CLI
  helper flows.

## Non-Goals

- Do not add or change CLI commands yet.
- Do not change installer behavior or version metadata as part of support
  bundle planning.
- Do not collect packet captures, browser storage, raw credentials, private
  keys, full home-directory listings, or unrelated application logs.

## Current v1.1.0 Implementation

The implemented web support bundle is intentionally limited but useful:

- `GET /v1/diagnostics/support_bundle` requires the same API token as other
  diagnostics endpoints.
- The endpoint returns a `.zip` archive with `Content-Type: application/zip`
  and a timestamped `vr-hotspot-support-bundle-YYYYMMDD-HHMMSS.zip` filename.
- Current archive content includes `manifest.json`, `README.txt`,
  `vr-hotspot/version.json`, `vr-hotspot/status.json`,
  `vr-hotspot/adapters.json`, and `vr-hotspot/readiness.json`.
- API tokens, passphrases, private keys, PSKs, emails, usernames, public IPs,
  and MAC addresses are redacted before files enter the archive.
- The Pro web UI includes a "Download support bundle" action.

Not yet implemented: CLI export, full systemd/journal/firewall/wireless command
collectors, tar.gz output, query parameters, and user opt-ins for expanded
client identifiers.

## Collection Scope

The support bundle should collect current facts only. It should avoid long
history, full journal exports, or anything that is not directly useful for VR
Hotspot diagnostics.

Required collection items:

- OS release:
  - `/etc/os-release` when readable.
  - Fallback command: `hostnamectl` where available.
- Kernel version:
  - `uname -a`.
  - `uname -r` as a concise field in the manifest.
- VR Hotspot version:
  - Runtime package/app version where available.
  - Backend version field where available.
  - Installed path and commit/build metadata only if already exposed by the
    application or installer state.
- Service status:
  - `systemctl status vr-hotspotd.service --no-pager`.
  - `systemctl is-enabled vr-hotspotd.service` when available.
  - `systemctl show vr-hotspotd.service` limited to useful fields such as
    `ActiveState`, `SubState`, `Result`, `ExecMainStatus`, `FragmentPath`,
    `UnitFileState`, and timestamps.
- Recent service logs:
  - `journalctl -u vr-hotspotd.service -n 300 --no-pager`.
  - Prefer a bounded time window or line count.
  - Never include unbounded journal output.
- Adapter inventory:
  - Existing `/v1/adapters` output when available.
  - Future normalized Adapter Intelligence v2 adapter inventory.
- Adapter Intelligence readiness output:
  - Future `GET /v1/adapters/readiness` response.
  - Include reason codes, readiness states, and recommendation fields.
- Wireless command output:
  - `iw dev`.
  - `iw list`.
  - `iw reg get`.
- Radio block state:
  - `rfkill list`.
- NetworkManager device status:
  - `nmcli device status`.
- Firewall state where available:
  - firewalld state, active zones, relevant zone details, and masquerade state
    via `firewall-cmd` when installed.
  - nftables or iptables summaries only where already used by the platform or
    needed to explain service failures.
- ufw status where available:
  - `ufw status verbose` when installed.
- Configuration with secrets redacted:
  - VR Hotspot environment/config files used by the backend.
  - Runtime config returned by future config diagnostics APIs.
  - Include key names and non-sensitive values needed for debugging.

Optional collection items:

- Installer state file or install log if VR Hotspot already writes one.
- Python/backend dependency versions if exposed by the application.
- Bundled binary versions for `hostapd`, `dnsmasq`, `lnxrouter`, and bundled
  libraries where available.
- Current hotspot status from `GET /v1/status` with sensitive fields sanitized.
- Connected-client diagnostics only if MAC addresses and client names are
  redacted by default.

## Redaction Requirements

Redaction must happen before writing the final files that enter the archive.
The implementation may collect raw command output in memory or in a protected
temporary directory, but raw files should not remain after generation.

Values that must be redacted:

- API token:
  - `VR_HOTSPOTD_API_TOKEN`.
  - Authorization headers.
  - Token query parameters or copied curl examples.
- Wi-Fi passphrase:
  - Hotspot passphrase/config key values.
  - WPA/WPA2/WPA3 passphrases in generated or saved config.
- Private keys:
  - PEM/OpenSSH private key blocks.
  - WireGuard/private VPN keys if they appear in logs or config.
- PSKs:
  - `psk=...`.
  - `wpa_passphrase=...`.
  - `sae_password=...`.
  - Any key ending in `_PSK`, `_PASSWORD`, `_PASSPHRASE`, `_SECRET`, or
    `_TOKEN`.
- Emails and usernames if present in logs:
  - Email addresses should be replaced with stable placeholders such as
    `<redacted-email-1>`.
  - Usernames in home paths, shell prompts, or log prefixes should be replaced
    with stable placeholders such as `<redacted-user-1>` when detectable.
- Public IPs if present:
  - Public IPv4 and IPv6 addresses should be redacted by default.
  - Private, link-local, loopback, and carrier-grade NAT addresses may remain
    when useful for local-network diagnosis, but the manifest should state this
    policy.
- MAC addresses unless explicitly needed:
  - Default behavior should redact MAC addresses to stable placeholders such as
    `<redacted-mac-1>`.
  - Preserve adapter correlation by replacing the same MAC with the same
    placeholder throughout the bundle.
  - If a future advanced option includes MAC addresses, it must require explicit
    user opt-in and the bundle manifest must record that opt-in.

Recommended redaction behavior:

- Use structured config parsing where possible before falling back to line
  redaction.
- Apply redaction to every text artifact, including `manifest.json`.
- Use stable placeholders within one bundle so support can correlate repeated
  values without seeing the original value.
- Preserve enough shape to debug, such as key names, interface names, private
  IP prefixes, and command names.
- Treat unknown config keys containing sensitive words as secret-bearing until
  proven otherwise.

## Output Format

The bundle should be a compressed archive:

- Preferred default: `.zip`, because GitHub issue attachments support it and
  it is easy for users on other systems to inspect.
- Acceptable alternative: `.tar.gz`, especially for CLI-only Linux workflows.
- Archive name: `vr-hotspot-support-bundle-YYYYMMDD-HHMMSS.zip` or
  `vr-hotspot-support-bundle-YYYYMMDD-HHMMSS.tar.gz`.

Every archive should contain:

```text
manifest.json
README.txt
system/
  os-release.txt
  kernel.txt
  command-results.json
service/
  status.txt
  show.json
  journal.txt
vr-hotspot/
  version.json
  status.json
  adapters.json
  readiness.json
  config.redacted.json
wireless/
  iw-dev.txt
  iw-list.txt
  iw-reg-get.txt
  rfkill-list.txt
network/
  nmcli-device-status.txt
  firewall.txt
  ufw-status.txt
```

`manifest.json` should include:

- `bundle_schema_version`.
- `generated_at`.
- `vr_hotspot_version`.
- `hostname_redacted` or an explicit note that hostname was not collected.
- `platform_summary`.
- `redaction_policy`.
- `files` with path, content type, collector name, success/failure state, and
  size.
- `command_results` with command, exit code, timeout state, permission state,
  and sanitized error summary.
- `warnings` for incomplete collection, missing tools, permission limits, or
  explicit user opt-ins.

Example manifest shape:

```json
{
  "bundle_schema_version": 1,
  "generated_at": "2026-05-09T12:00:00Z",
  "vr_hotspot_version": "1.0.4",
  "platform_summary": {
    "os_id": "steamos",
    "os_version_id": "3",
    "kernel_release": "6.8.0"
  },
  "redaction_policy": {
    "secrets": "redacted",
    "emails_usernames": "redacted_when_detected",
    "public_ips": "redacted",
    "mac_addresses": "redacted_by_default"
  },
  "files": [
    {
      "path": "wireless/iw-dev.txt",
      "collector": "iw dev",
      "status": "ok",
      "content_type": "text/plain",
      "size_bytes": 842
    }
  ],
  "command_results": [
    {
      "command": "iw dev",
      "exit_code": 0,
      "status": "ok"
    }
  ],
  "warnings": []
}
```

## Current API Endpoint

Authenticated endpoint:

```text
GET /v1/diagnostics/support_bundle
```

Expected behavior:

- Requires the same authentication model as other sensitive diagnostics routes.
- Streams the archive as a download.
- Uses `Content-Type: application/zip` for the current `.zip` output.
- Sets a filename with `Content-Disposition`.
- Generates the bundle on demand.
- Applies redaction before response streaming.
- Records failures inside `manifest.json` instead of failing the whole request
  for optional collectors.
- Returns a normal API error only when bundle generation cannot safely create a
  sanitized archive at all.

Potential future query parameters:

- `format=zip|tar.gz`.
- `include_clients=0|1`, default `0` unless client identifiers are redacted.
- `include_mac_addresses=0|1`, default `0` and requiring explicit warning.
- `logs_lines=100..1000`, bounded by a server-side maximum.

The endpoint must not return raw logs, raw config, or unsanitized intermediate
collector output.

## Future CLI or Helper Command

Future command candidates:

```bash
vr-hotspot diagnostics support-bundle
```

or, if the project keeps helper scripts first:

```bash
sudo /var/lib/vr-hotspot/app/backend/scripts/vr-hotspot-support-bundle
```

Expected CLI behavior:

- Print the output archive path.
- Work without root where possible, while clearly recording permission-limited
  collectors.
- Recommend `sudo` only when it materially improves collection, such as access
  to service logs, protected config files, or firewall details.
- Never print secrets to the terminal.
- Provide `--output <path>` for explicit destination.
- Provide `--format zip|tar.gz`.
- Provide `--logs-lines <n>` with a safe maximum.
- Provide `--dry-run` to show collectors and permissions without writing a
  bundle.

The CLI should use the same collector and redaction implementation as the web
endpoint so archive structure and privacy behavior stay consistent.

## Missing Commands and Permission Failures

Bundle generation should be failure-tolerant. A missing command or permission
failure should not prevent the archive from being created unless it prevents
redaction or archive writing.

Collector result states:

- `ok`: command or file was collected and sanitized.
- `missing_command`: executable was not found.
- `permission_denied`: command or file needs elevated access.
- `timeout`: command exceeded the collector timeout.
- `not_applicable`: platform does not use the subsystem, such as `ufw` on a
  firewalld-only install.
- `failed`: command returned a non-zero exit code for another reason.
- `redaction_failed`: sanitized output could not be produced; omit the file and
  record the failure.

Failure handling rules:

- Include a sanitized stderr summary when useful.
- Include command exit code and timeout state in `manifest.json`.
- Do not include partial raw output when redaction fails.
- Use short per-command timeouts so bundle generation cannot hang on a broken
  network or service manager.
- Treat optional firewall tools as `not_applicable` or `missing_command` rather
  than fatal.
- If the API route cannot access service logs because it runs unprivileged,
  record `permission_denied` and suggest the future CLI path in `README.txt`.

## Security and Privacy Warnings

The web UI and CLI should show a clear warning before generation:

- The bundle is intended for public GitHub issues only after the user reviews
  it.
- Secrets are redacted automatically, but users should still inspect the archive
  before uploading it.
- The bundle may reveal system details such as OS version, kernel version,
  distro, Wi-Fi adapter model, driver, firewall mode, local IP ranges, and
  service failure messages.
- MAC addresses, public IPs, emails, usernames, tokens, passphrases, private
  keys, and PSKs are redacted by default.
- Users should not attach manually edited raw logs or config files in place of
  the sanitized bundle.

Implementation security requirements:

- Create temporary files in a private directory with restrictive permissions.
- Avoid world-readable output paths by default.
- Delete raw temporary files after successful generation or failure cleanup.
- Never log raw secret-bearing config while generating the bundle.
- Bound archive size and log line counts.
- Do not follow arbitrary symlinks from protected config paths.
- Do not include environment dumps wholesale; include only allowlisted config
  keys after redaction.

## Future Testing Plan

Unit tests:

- Redacts API tokens in env files, JSON, logs, curl snippets, and headers.
- Redacts Wi-Fi passphrases, WPA PSKs, SAE passwords, and generic secret-like
  keys.
- Redacts PEM/OpenSSH private key blocks.
- Redacts emails, usernames, public IP addresses, and MAC addresses with stable
  placeholders.
- Preserves repeated placeholder correlation within one bundle.
- Leaves useful non-sensitive facts intact, such as interface names, driver
  names, local private IP ranges, and readiness reason codes.
- Produces expected `manifest.json` for successful collectors.
- Records `missing_command`, `permission_denied`, `timeout`,
  `not_applicable`, `failed`, and `redaction_failed` states.

Integration tests:

- Generates a `.zip` bundle with the expected directory structure and sanitized
  files.
- Generates a `.tar.gz` bundle when requested.
- Handles missing `iw`, `rfkill`, `nmcli`, `firewall-cmd`, and `ufw` without
  failing the whole bundle.
- Handles unavailable systemd/journalctl environments.
- Streams `GET /v1/diagnostics/support_bundle` with the expected content type,
  filename, and authentication behavior once implemented.
- Verifies the future CLI/helper command writes the archive and prints the
  output path without printing secrets.

Manual tests:

- SteamOS or SteamOS-like firewalld system.
- Bazzite/CachyOS/Arch system with NetworkManager.
- Ubuntu/Pop!_OS system with ufw.
- System with no Wi-Fi adapter attached.
- System with internal `wlan0` plus external USB adapter.
- Permission-limited non-root collection followed by elevated CLI collection.
- User review of a generated archive before attaching it to a GitHub issue.
