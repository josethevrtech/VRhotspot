# Flatpak control app architecture plan

Status: PR #77 documentation-only architecture plan

Date: 2026-07-23

This document defines the boundary for a possible future Flatpak control
application for VRhotspot. It does not add a Flatpak package, manifest, desktop
application, API endpoint, UI, or runtime behavior. The Flatpak application
does not exist yet.

## Architectural decision

The future Flatpak is an unprivileged control panel and API client. The
existing host-installed `vr-hotspotd` remains the only privileged authority.
The existing Web UI and the future Flatpak are sibling clients of the daemon;
the Flatpak must not copy daemon policy or execute host-management commands.

```text
+-----------------------------+
| Future Flatpak control app  |
| unprivileged UI/API client  |
+--------------+--------------+
               |
               | authenticated local HTTP API
               | default target: 127.0.0.1:8732
               v
+--------------+--------------+
| Host-installed vr-hotspotd  |
| privileged policy authority |
+--------------+--------------+
               |
               +-- lifecycle and configuration
               +-- NetworkManager and iwd
               +-- hostapd and dnsmasq
               +-- firewall, routes, NAT, and network tuning
               +-- systemd integration
               +-- diagnostics and support-bundle redaction
```

The daemon continues to own host networking, systemd, NetworkManager/iwd,
hostapd, dnsmasq, firewall state, lifecycle, diagnostics, and support bundles.
The Flatpak may request an operation through the authenticated daemon API and
render its response, but it must not perform that operation directly.

### Component ownership

| Component | Responsibility |
|---|---|
| Flatpak control app | Present Basic/Pro control concepts, collect intentional user input, call the local API, render safe results, and export daemon-produced reports through a portal. |
| Local daemon API | The only control and diagnostic boundary between the sandboxed app and privileged host behavior. It authenticates every `/v1/*` request and returns stable, sanitized contracts. |
| `vr-hotspotd` | Validate policy, select and protect adapters, mutate host networking, manage lifecycle, collect host diagnostics, and generate redacted support bundles. |
| Existing host installer | Install, configure, update, repair, and remove the daemon, systemd units, host dependencies, and privileged integration. The Flatpak does not take over this ownership. |
| Flatpak distribution | Distribute only the unprivileged client and its declared application dependencies. It does not embed or update the daemon's privileged networking payload. |

The Flatpak must tolerate a missing, stopped, too-old, or incompatible daemon
and explain the condition without trying to install, start, repair, or replace
the host service itself. Host installation and update guidance may point to the
existing supported installation process, but the control app must not shell
out to it.

## Local API boundary

The target transport is the daemon's local HTTP API, which defaults to
`127.0.0.1:8732`. The first client prototype should pin requests to an explicit
loopback origin, reject redirects, and avoid accepting arbitrary remote base
URLs. A future need for port discovery or a different local transport requires
a separately reviewed design; this plan does not add D-Bus, a Unix socket, or
another daemon interface.

The client must treat API calls as requests to a privileged authority:

- The daemon validates and executes lifecycle and configuration requests.
- The client does not infer success from an HTTP connection alone; it consumes
  the response envelope, `result_code`, warnings, and returned state.
- The daemon remains authoritative for adapter selection, active-uplink safety,
  Basic Mode enforcement, firewall policy, repair, and fallback behavior.
- Read-only facts come from API responses. The Flatpak does not run `iw`,
  `nmcli`, `systemctl`, firewall tools, `ip`, `hostapd`, or `dnsmasq`.
- A support bundle is generated and redacted by the daemon before the Flatpak
  receives it. The client does not assemble a second bundle from host files.

### Contract requirements

Future client work requires a stable, documented local contract:

- Keep the authenticated `/v1/*` namespace and the existing response envelope
  compatible for supported daemon/client version combinations.
- Use explicit application and API compatibility information. The current
  `/v1/info` version fields can inform compatibility, but the exact negotiation
  and minimum-version policy remain to be designed.
- Preserve endpoint method, content type, field meaning, result-code, warning,
  and attachment semantics that a released client depends on.
- Additive response fields should be tolerated. Removing or changing a field,
  result code, or meaning requires an intentional compatibility decision.
- Timeouts, connection refusal, malformed responses, unsupported versions, and
  daemon-side failures must map to safe client states rather than retries that
  repeat mutations.
- Error messages shown to users should be actionable and sanitized. The client
  should not display raw tracebacks, command output, secrets, environment
  values, or avoidable absolute host paths.

This plan does not freeze every current endpoint as a permanent public API and
does not add API behavior. PR #78 must first inventory the exact calls its
prototype needs and characterize their current responses with unit tests.

### Basic and Pro mode compatibility

Basic and Pro are presentation and policy concepts already used by the Web UI
and daemon. A future Flatpak should preserve them instead of creating a
conflicting mode model:

- Basic mode presents the small, conservative control surface and sends the
  existing Basic Mode intent where the API supports it.
- Pro mode can expose additional daemon-provided configuration and diagnostics
  without bypassing daemon validation.
- Hidden Basic-mode fields are not permission boundaries. The daemon remains
  responsible for validating all requests from either mode.
- Readiness, recommendations, result codes, and warnings come from the daemon;
  the Flatpak must not maintain a competing adapter-policy implementation.

## Authentication and pairing boundary

The current boundary is token authentication using `X-Api-Token` or a Bearer
token. Every `/v1/*` route remains protected. If
`VR_HOTSPOTD_API_TOKEN` is missing or blank in the daemon environment, the
daemon's fail-closed `503` / `api_token_missing` behavior must be preserved.
The Flatpak must explain that the host daemon needs configuration; it must not
mint a replacement token locally or treat the failure as an unauthenticated
setup mode.

The first API-client prototype in PR #78 may accept a manually entered token.
PR #79 is responsible for a separately reviewed token pairing and first-run
flow. That design must meet these requirements:

- Pairing is authorized and completed by the daemon. The sandbox cannot read
  `/etc/vr-hotspot/env` or other protected host configuration.
- Initial pairing requires an explicit, local user action and must not expose
  a long-lived token in a URL, process argument, notification, or log.
- Whether pairing reuses the current daemon token or issues a derived,
  revocable credential remains unresolved. It must not weaken the current
  token requirement.
- Token rotation, invalidation, and authorization decisions remain daemon
  responsibilities. The client handles `401`/`403` by discarding invalid
  credentials and returning to pairing or login.
- Tokens are attached only to the pinned local origin and are never forwarded
  across redirects.
- Tokens are not logged, included in analytics, copied into support bundles,
  or exposed in error text. Debug output may record only non-secret facts such
  as whether a credential was present.
- Long-lived credentials should use an appropriate desktop secret-storage
  mechanism available to the sandbox. App-private files are not automatically
  equivalent to secret storage. If safe storage is unavailable, the client
  should prefer an in-memory session and prompt again.

The pairing threat model, credential scope, recovery path, rotation behavior,
and storage backend must be documented before PR #79 is considered complete.

## Sandbox and portal expectations

The future Flatpak should begin from a minimal permission set and justify every
addition.

- Local loopback access to `vr-hotspotd` is required. Flatpak network sharing
  is broader than a loopback-only grant, so a future manifest must describe
  that exposure honestly and the client must still restrict its destination to
  the approved local origin.
- Do not grant system-bus access to NetworkManager, iwd, firewalld, systemd, or
  other services for host mutation.
- Do not grant device access, kernel/network-namespace control, host command
  execution, or capabilities intended to alter interfaces, routes, firewall
  rules, or namespaces.
- Do not grant broad host or home filesystem access by default. App-private
  state should contain only the minimum non-secret client preferences and
  caches.
- Use a file chooser/export portal when the user chooses where to save a
  daemon-produced support bundle or report. A portal-selected destination is
  not permission to scan the surrounding directory.
- Use the notification portal only if later UX work demonstrates a need, such
  as reporting completion of an explicitly requested operation. Notifications
  must not contain tokens, passphrases, private network identifiers, or raw
  diagnostics.
- Clipboard use, background operation, autostart, and any additional portal or
  permission require explicit review rather than being assumed by this plan.

The Flatpak may have ordinary client network capability solely to reach the
local API, but it receives no authority to mutate host networking. Permission
review must consider both declared Flatpak permissions and reachable D-Bus or
portal interfaces.

## Diagnostics, support bundles, and reporting

The daemon remains the reporting authority:

1. The Flatpak makes an authenticated request to the local diagnostics or
   support-bundle endpoint.
2. The daemon collects bounded host facts using its existing privilege and
   policy boundary.
3. The daemon applies the existing support-bundle redaction rules and produces
   the sanitized archive.
4. The Flatpak offers that returned archive through a file export portal.
5. The user is reminded to review the archive before sharing it.

The Flatpak must not read the journal, `/etc`, `/run`, `/var/lib`, daemon
configuration, environment files, or host network state to enrich the bundle.
It must not weaken or duplicate redaction. If the daemon cannot safely produce
a sanitized artifact, the client shows a safe failure and does not fall back to
raw logs or host file collection.

Downloads should use daemon-provided attachment metadata only after sanitizing
the filename. Temporary client-side copies, if unavoidable, must be private,
bounded, cleaned up, and contain only the already-sanitized daemon output.

## Security and privacy posture

- Secrets stay out of Flatpak logs, crash reports, notifications, telemetry,
  URLs, and support text.
- API tokens and Wi-Fi passphrases must never appear in normal logs. Request
  headers and bodies containing them must not be dumped.
- Saved passphrases are not fetched, displayed, or persisted by default. Any
  future explicit reveal/copy/QR flow requires a deliberate user action,
  short-lived in-memory handling, masking, and separate security review.
- The Flatpak must not dump its environment or enumerate host environment
  files. Diagnostics use allowlisted daemon responses.
- UI messages should prefer stable identifiers and sanitized summaries. Avoid
  exposing absolute host paths such as daemon capture or installation paths
  when a basename, logical label, or result code is sufficient.
- Support-bundle redaction remains authoritative even if the client adds a
  preview or export workflow.
- No cloud account, remote telemetry, or internet service is required for
  normal control operation. Any future network service would need explicit
  privacy, permission, and offline-behavior review.
- The local API token is still a privileged credential even though the target
  is loopback. Other local processes and desktop-session compromise remain in
  the threat model.

## Proposed phases

| Phase | Scope | Exit condition |
|---|---|---|
| PR #77 | Architecture plan only. No package, manifest, API, UI, runtime, installer, CI, or vendor change. | This document makes ownership, API/authentication, sandbox, privacy, support-bundle, distribution, and non-goal boundaries reviewable. |
| PR #78 | Flatpak local API client prototype. Start with a small client layer against a fake/mock daemon, explicit loopback pinning, authentication-header handling, redirect rejection, bounded timeouts, compatibility/error mapping, and no host command execution. | Unit tests demonstrate safe request construction and failure handling without privileged host mutation. Exact packaging scope requires separate approval. |
| PR #79 | Token pairing and first-run flow. Define and implement the daemon-authorized pairing protocol, secure credential storage/recovery, missing-daemon and missing-token guidance, and version compatibility UX. | Pairing cannot bypass fail-closed daemon auth, leak tokens, or read protected host configuration; security tests cover expiry, rotation, rejection, and interrupted first run. |
| PR #80 | Flatpak diagnostics/control UI. Implement reviewed Basic/Pro views using the client contract, including daemon-owned status, lifecycle controls, diagnostics, and support-bundle export. | UI behavior is tested, sandbox permissions remain minimal, and all privileged effects are mediated by authenticated daemon calls. |
| Later, separately approved work | Steam Frame and VR Direct Link evidence-based research, followed by any separately approved adapter-intelligence work. | Work begins from lawful public or user-provided evidence and does not claim support before hardware, driver, regulatory, and security validation. |

Each phase is independently reviewable. A later phase is not authorized merely
because it appears in this roadmap.

## Packaging and distribution questions

The following decisions are deliberately unresolved and must be answered before
a production Flatpak release:

| Question | Required decision |
|---|---|
| Application ID | Select a stable reverse-DNS ID and confirm naming/ownership before creating manifests or published metadata. |
| Permissions | Document the minimum finish-args and portals, the practical breadth of network sharing, and the reason for every exception. |
| Runtime and SDK | Select supported versions, update cadence, end-of-life policy, architecture targets, and reproducible/offline build expectations. |
| Desktop file | Define name, categories, startup behavior, actions, and whether any URL scheme is justified. |
| Icons and metainfo | Produce reviewed icon sizes and AppStream/metainfo with accurate screenshots, releases, licenses, privacy statements, and no unsupported feature claims. Existing Web UI assets are not automatically release-ready desktop metadata. |
| Local daemon discovery | Decide how to detect an installed/compatible daemon without broad filesystem access, service-manager control, or token leakage. |
| Offline/local-first behavior | Confirm that installed control functions need no cloud service; define behavior when the internet is unavailable and distinguish that from daemon unavailability. |
| Versioning and compatibility | Define client, API, and daemon compatibility ranges, upgrade ordering, unsupported-version messaging, and rollback expectations. |
| Release process | Define source provenance, dependency review, reproducible build inputs, signing, store submission, release notes, and coordination with host-daemon releases. |
| Installation/update ownership | Keep daemon installation and privileged updates separate from Flatpak updates; document how users avoid incompatible independent versions. |

No candidate answer in this table is a packaging implementation or permission
approval.

## Future test and validation expectations

Future implementation PRs should add proportionate tests without invoking real
host networking or service commands:

- API client unit tests for URL/origin pinning, redirect rejection,
  authentication headers, token non-disclosure, response envelopes, binary
  attachments, timeouts, connection refusal, malformed data, safe error
  mapping, and supported/unsupported daemon versions.
- Authentication tests for `401`/`403`, the existing fail-closed
  `api_token_missing` response, pairing interruption, credential rotation, and
  credential removal.
- UI smoke or behavior tests using the existing test stack where it can execute
  the future UI safely; add a new UI framework only through a separate
  dependency and supply-chain review.
- Basic/Pro compatibility tests that verify the client renders daemon policy
  and does not create a competing adapter recommendation or lifecycle policy.
- Sandbox permission review of the built artifact, declared finish-args,
  portals, D-Bus access, filesystem access, device access, and network reach.
- Static and behavior-level checks that the Flatpak never invokes host
  networking tools, mutates firewall/routing state, controls systemd services,
  or writes privileged host paths.
- Support-bundle export tests covering authenticated download, sanitized
  filenames, portal success/cancellation, bounded temporary handling, and no
  fallback to raw host data.
- Offline tests showing normal local controls work without internet access and
  clearly distinguish client, daemon, authentication, and uplink failures.
- Release validation that client/daemon compatibility metadata and
  supply-chain records match the shipped artifacts.

## Supply-chain continuity

PRs #72 through #76 completed the staged vendor provenance, canonical
`backend/vendor/` manifest, deterministic vendor-only SBOM generation,
source-tree SHA-256 validation, and bounded support-bundle vendor provenance
reporting. Flatpak work must not weaken or bypass those controls.

- Do not hide daemon or privileged vendor payloads inside the Flatpak.
- Flatpak runtimes, SDKs, libraries, JavaScript/UI dependencies, build tools,
  and generated assets need documented sources, licenses, versions, checksums,
  and update ownership appropriate to their distribution path.
- The current backend vendor manifest is not automatically a complete Flatpak
  SBOM. Define the new package's inventory and SBOM scope explicitly instead of
  overclaiming repository-wide coverage.
- Builds and validation should be deterministic and offline after reviewed
  sources are available; unpinned downloads during build or release are not an
  acceptable provenance process.
- The daemon's vendor-provenance support report remains diagnostic and
  reporting-only. The Flatpak must present it honestly and must not reinterpret
  it as runtime trust enforcement.
- No proprietary Steam or Valve driver, firmware, depot content, or helper may
  be downloaded, bundled, redistributed, or collected as a side effect of
  Flatpak installation or use.

## Explicit non-goals for PR #77

- No Flatpak packaging, manifest, build definition, finish-args, desktop file,
  icon set, metainfo, repository submission, or release artifact.
- No Flatpak UI or other frontend code.
- No daemon endpoint, API response, authentication, pairing, lifecycle,
  diagnostics, support-bundle, or runtime behavior change.
- No installer, uninstaller, systemd-unit, platform, or CI behavior change.
- No privileged networking in the Flatpak.
- No direct Flatpak control of firewall rules, NetworkManager, iwd, hostapd,
  dnsmasq, systemd units, kernel or network namespaces, interfaces, routes,
  NAT, or other privileged host mutation.
- No broad host filesystem access and no direct reading of daemon secrets,
  configuration, logs, or state.
- No vendor file or vendor manifest change.
- No bundled or automatically downloaded proprietary Steam/Valve drivers,
  firmware, utilities, or depot content.
- No Steam Frame implementation or support claim.
- No VR Direct Link implementation or support claim.
- No known-adapter registry or adapter-policy implementation.
- No HostFactsSnapshot work or consumer change.

PR #77 authorizes only this architecture plan. It does not claim that a
Flatpak, Steam Frame support, VR Direct Link support, or a known-adapter
registry exists.
