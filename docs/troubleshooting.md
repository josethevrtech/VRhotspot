# Troubleshooting

This guide collects diagnostic commands, common issues, and validation
checklists for VR Hotspot.

## Check service status

```bash
sudo systemctl status vr-hotspotd.service
```

## View logs

```bash
# Recent logs
sudo journalctl -u vr-hotspotd.service -n 100

# Follow logs in real-time
sudo journalctl -u vr-hotspotd.service -f
```

## Check API status

```bash
# Get API token
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)

# Check status
curl -s "http://127.0.0.1:8732/v1/status?include_logs=1" -H "X-Api-Token: $TOKEN" | python3 -m json.tool
```

## Preflight diagnostics

Installed systems provide a read-only CLI that calls the existing authenticated
preflight endpoint. Running it with `sudo` lets it read the protected daemon
token from `/etc/vr-hotspot/env` without putting the token in shell history or
process arguments:

```bash
# Print the canonical report as formatted JSON
sudo /var/lib/vr-hotspot/bin/vr-hotspot preflight

# Export through stdout into a new private, user-owned directory and file
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/vr-hotspot-preflight.XXXXXX")"
(umask 077; sudo /var/lib/vr-hotspot/bin/vr-hotspot preflight \
  > "$report_dir/preflight.json")
```

`--output PATH` is also available when the CLI should create the file directly.
It creates a new file with mode `0600` and refuses existing paths and symlinks;
choose a private destination rather than a predictable shared `/tmp` filename.

For development or custom API locations, use `--api-url` and provide the token
through `VR_HOTSPOTD_API_TOKEN` or stdin. For a one-off prompt that does not echo
the token or store it in shell history:

```bash
read -rsp 'VR Hotspot API token: ' API_TOKEN && echo
printf '%s\n' "$API_TOKEN" | vr-hotspot preflight \
  --api-url http://127.0.0.1:8732 --token-stdin
unset API_TOKEN
```

The `--token` option is available for automation compatibility, but its value is
visible in process arguments and may be retained in shell history; prefer the
protected env file, environment variable, or `--token-stdin`. The CLI rejects
redirects and only performs `GET /v1/diagnostics/preflight`; it does not probe
the host directly or mutate hotspot state.

## Common issues

**1. No Wi-Fi adapters found:**
- Check: `iw dev`
- Ensure adapter supports AP mode: `iw list | grep -A10 "Supported interface modes"`

**2. Hotspot times out (ap_ready_timeout):**
- Check if NetworkManager is interfering: `nmcli device status | grep wlan`
- Try using `wlan1` instead of `wlan0`
- Check logs: `sudo journalctl -u vr-hotspotd.service -n 50`

**3. Can't access web UI:**
- Check firewall: `sudo firewall-cmd --list-ports` or `sudo ufw status`
- Verify service is running: `curl http://127.0.0.1:8732/healthz`

**4. Intel AX200 (wlan0) not working:**
- This is a known hardware limitation
- Use wlan1 (USB adapter) instead
- See: `../BUNDLED_LIBNL_SETUP.md`

## Repair function

If the hotspot gets stuck, use the **Repair** button in the web UI or:

```bash
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)
curl -X POST "http://127.0.0.1:8732/v1/repair" -H "X-Api-Token: $TOKEN"
```

## Support bundle

For bug reports, use **Download support bundle** in Pro mode or call the
authenticated endpoint directly:

```bash
TOKEN=$(sudo awk -F= '/VR_HOTSPOTD_API_TOKEN/{print $2}' /etc/vr-hotspot/env)
curl -OJ "http://127.0.0.1:8732/v1/diagnostics/support_bundle" -H "X-Api-Token: $TOKEN"
```

The bundle is a sanitized `.zip` with version, status, adapter inventory, and
readiness data. Review it before attaching it to a public issue. See
`support-bundle.md` for the full design and redaction details.

## SteamOS validation

SteamOS 3.8.12 stable has been validated working.

Validated result:

- SteamOS 3.8.12 stable
- bundled hostapd/dnsmasq/lnxrouter stack
- AP interface x0wlan1
- 5 GHz channel 36
- 80 MHz width
- client association and WPA handshake confirmed
- internet and streaming confirmed working

Validation checklist:

```bash
systemctl status vr-hotspotd.service --no-pager
ls -lah /var/lib/vr-hotspot/app/backend/vendor/bin
sudo grep -E 'VR_HOTSPOT.*VENDOR|VR_HOTSPOT_FORCE_VENDOR_BIN|VR_HOTSPOTD_HOST|VR_HOTSPOTD_PORT' /etc/vr-hotspot/env
curl -fsS http://127.0.0.1:8732/healthz && echo OK
iw dev
iw dev x0wlan1 station dump
```

## Filing a bug

**When filing a bug, please include:**

- OS/distro + kernel version
- Wi-Fi adapter chipset/model
- A sanitized support bundle from Pro mode or
  `GET /v1/diagnostics/support_bundle`
- If you cannot generate a support bundle, include
  `sudo journalctl -u vr-hotspotd.service -n 200` and
  `curl http://127.0.0.1:8732/v1/status?include_logs=1`
- Redact any API tokens or passwords from manually collected output

See `../CONTRIBUTING.md` for more details.
