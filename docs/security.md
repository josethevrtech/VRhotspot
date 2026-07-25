# Security & Privacy

For reporting vulnerabilities, see the repository's `SECURITY.md`.

## Privileged API threat model

The portal shell, static assets, favicon, and `/healthz` are public. Every
`/v1/*` route controls or exposes privileged daemon state and requires both a
configured `VR_HOTSPOTD_API_TOKEN` and a matching `X-Api-Token` or Bearer token.
If the daemon token is absent or blank, privileged requests fail closed with
HTTP 503 and `result_code: api_token_missing`; supplying a token in the browser
cannot repair a missing daemon configuration. Configure the token in the daemon
environment (normally `/etc/vr-hotspot/env`) and restart the service.

The daemon runs as root, so the token protects network mutation, diagnostics,
configuration, and passphrase-reveal operations from other local processes as
well as network clients. Tokenless non-loopback binds are refused. Remote access
still uses plain HTTP and is trusted-network-only: use it only on a network where
traffic cannot be observed or modified. This project does not currently provide
built-in TLS termination.

## API token protection

- **Treat the token like a password** - don't share it publicly
- **Token enforcement** prevents unauthorized access
- Regenerate token if compromised: Edit `/etc/vr-hotspot/env` and restart service

## Privacy Mode

- Enable **Privacy Mode** in the web UI when:
  - Screen sharing
  - Taking screenshots
  - Collecting logs for support
- Hides sensitive information (logs, client details, etc.)

## Remote access

- By default, the web UI only listens on `127.0.0.1` (local only)
- To allow remote access: `sudo ./install.sh --bind 0.0.0.0`
- **Important**: A strong token is mandatory, but remote HTTP is still suitable
  only for a trusted network
