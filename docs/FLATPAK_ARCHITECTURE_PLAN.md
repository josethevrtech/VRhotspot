# Flatpak control app architecture plan

Status: PR #81 Flatpak packaging/app shell prototype; PRs #77-#80 retained

Date: 2026-07-23

This document defines the boundary for the VRhotspot Flatpak control
application. PR #81 adds the first rough installable and testable Flatpak app
shell on top of PR #78's read-only local API client, PR #79's pairing state, and
PR #80's toolkit-agnostic UI model/controller foundation. It proves packaging,
launching, a deterministic standard-library smoke path, and a lazy GTK 4
placeholder window. It is not a finished production UI and adds no daemon API,
daemon runtime, installer, privileged action, or credential-storage behavior.

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

### PR #78 prototype

PR #78 adds `flatpak_client/`, a top-level Python prototype kept separate from
both the privileged `vr_hotspotd` daemon package and the existing Web UI. It is
not included in daemon packaging and does not add Flatpak packaging metadata.
The prototype uses only the Python standard library and exposes three
read-only methods:

- `health()` performs `GET /healthz`.
- `preflight_report()` performs `GET /v1/diagnostics/preflight`.
- `adapter_readiness()` performs `GET /v1/adapters/readiness`.

The client accepts its token explicitly and does not discover, read, pair,
store, rotate, or persist credentials. Authenticated requests use
`X-Api-Token`; request and client representations redact or omit the token, and
sanitized exceptions do not retain transport exception chains. The default
origin is `http://127.0.0.1:8732`; only literal IPv4 or IPv6 loopback HTTP
origins are accepted. Proxies and redirects are disabled by the standard
transport, and redirects returned by injected test transports are rejected.

The injectable transport makes the client offline-testable without a daemon or
live network. Response handling preserves the daemon envelope, bounds response
and error-body processing, rejects malformed JSON and invalid envelopes, and
distinguishes connection failures, authentication failures, and the daemon's
fail-closed `503` / `api_token_missing` response. There is no generic public
request method and no lifecycle, configuration, diagnostic execution, support
bundle generation, or other mutation method.

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

PR #79 accepts a token explicitly from its caller and asks the existing daemon
to validate it through the authenticated, read-only
`GET /v1/adapters/readiness` endpoint. In this foundation, "pairing" means that
the daemon accepted the existing credential; it does not mean credential
issuance, token exchange, or a new daemon-side pairing endpoint.

The `flatpak_client/pairing.py` controller first calls public `/healthz` without
a token. Health success proves daemon reachability only. Without a caller-
supplied token, the result remains `daemon_reachable_unpaired`. With a supplied
token, the controller creates a token-bearing `LocalApiClient` only for the
authenticated read-only validation call and maps the result to one of these
fixed states:

- `daemon_unreachable`
- `daemon_reachable_unpaired`
- `token_accepted`
- `token_rejected`
- `daemon_token_missing`
- `invalid_response`

The returned messages and detail codes are fixed, bounded, and token-free.
Client or transport exception text is not copied into pairing state. The
controller does not log the token, retain it after evaluation, discover it from
the environment or host files, or persist it. PR #79 adds no storage backend:
the caller must keep the token in memory for the current interaction and supply
it again when needed.

The PR #79 foundation has these boundaries:

- Pairing is authorized and completed by the daemon. The sandbox cannot read
  `/etc/vr-hotspot/env` or other protected host configuration.
- Initial pairing requires an explicit, local user action and must not expose
  a long-lived token in a URL, process argument, notification, or log.
- PR #79 validates the current daemon token. Whether a future pairing protocol
  issues a derived, revocable credential remains unresolved and requires
  separate daemon API review.
- Token rotation, invalidation, and authorization decisions remain daemon
  responsibilities. The controller maps `401`/`403` to `token_rejected`, so a
  future UI can return to token entry without retaining the rejected value.
- Tokens are attached only to the pinned local origin and are never forwarded
  across redirects.
- Tokens are not logged, included in analytics, copied into support bundles,
  or exposed in error text. Debug output may record only non-secret facts such
  as whether a credential was present.
- Long-lived credential storage remains future work and should use an
  appropriate desktop secret-storage mechanism available to the sandbox.
  App-private files are not automatically equivalent to secret storage. Until
  a backend is separately approved, the client uses an in-memory session and
  prompts again.
- Missing daemon, rejected token, missing daemon token, and malformed response
  recovery are represented as state only. Graphical guidance, compatibility
  UX, rotation workflows, and recovery controls remain future UI/API work.

## Diagnostics/control UI foundation

PR #80 adds `flatpak_client/ui.py`, a toolkit-agnostic view-model/controller
layer for a future polished Linux VR Wi-Fi control center. It has no GTK,
libadwaita, Qt, desktop-window, or Flatpak packaging dependency. The foundation
projects only the existing `LocalApiClient` responses and `FirstRunResult`
states into frozen, UI-ready models:

- daemon reachability and pairing status;
- adapter readiness summary and cards;
- preflight summary facts, issues, and non-interactive recommended actions;
- a visible but disabled support-bundle export affordance.

The UI controller calls only `adapter_readiness()` and `preflight_report()`,
and only after the supplied pairing result is `token_accepted`. It has no
generic request method, lifecycle control, configuration mutation, support-
bundle download, or export method. The support-bundle model states whether
pairing is required or export wiring is not implemented; it performs no daemon
request and remains disabled until bounded binary handling and a portal export
flow receive separate review.

The models use the presentation severities `ok`, `warning`, `blocked`, `error`,
and `unknown`. Known daemon readiness states map to those values; unrecognized,
malformed, partial, or failed responses degrade to bounded `unknown` sections
with fixed safe copy. Adapter recommendations and Basic-mode visibility remain
daemon-provided facts rather than client-side policy.

Basic and Pro are represented as presentation modes. Basic exposes the same
safe summary/status/card foundation with technical details hidden; Pro marks
those already-sanitized details as displayable later. The mode field does not
change daemon calls, authorize actions, filter adapters, or override daemon
policy, and PR #80 adds no mode-toggle widget.

Daemon content is projected through an allowlist rather than retained as raw
response dictionaries. Collections and strings are bounded, recognized secret
assignments and authorization credentials are redacted, unexpected secret or
environment fields are omitted, and avoidable absolute host paths are replaced
with a generic label. API tokens and Wi-Fi passphrases are not model fields.
The controller copies no exception text into UI state and exposes no raw
preflight report or response body.

## PR #81 packaging and app shell prototype

PR #81 adds `flatpak_app/` and the JSON manifest
`packaging/flatpak/io.github.josethevrtech.VRhotspot.json` for application ID
`io.github.josethevrtech.VRhotspot`. The manifest installs only the app shell,
the existing `flatpak_client` prototype, and simple static desktop metadata and
icon assets. It does not include the daemon package, privileged networking
code, systemd units, installers, or `backend/vendor/` payload.

The default launcher opens a rough GTK 4 placeholder when the selected GNOME
runtime supplies GTK and PyGObject. Toolkit loading is lazy, so importing the
shell and running repository tests do not require GTK on the development host.
The window is display-only: it starts from a safe offline/unpaired Basic-mode
model, does not contact the daemon, and contains no lifecycle, repair,
configuration, adapter-selection, or other privileged action.

The standard-library smoke path is:

```bash
python -m flatpak_app --smoke-json
```

It prints one bounded JSON document built from the existing pairing and
diagnostics/control UI concepts. It performs no network request, credential
discovery, host-file access, portal call, or persistence.

With the required Flatpak tooling and GNOME runtime available, a developer can
build, install, and launch the prototype from the repository root:

```bash
flatpak-builder --user --install --force-clean build-dir \
  packaging/flatpak/io.github.josethevrtech.VRhotspot.json
flatpak run io.github.josethevrtech.VRhotspot
```

The manifest grants ordinary network sharing only because Flatpak has no
loopback-only network permission and the existing client must eventually reach
the pinned local HTTP API. The client still permits only literal HTTP loopback
origins. The remaining finish arguments provide Wayland and fallback X11
display access. There is no filesystem permission, system-bus access, session-
bus name ownership, device permission, or host networking authority.

Real credential entry UI and live authenticated daemon wiring are not included.
The shell does not discover or persist credentials through files, environment,
keyrings, portals, or daemon configuration. Support-bundle export remains a
disabled placeholder; portal export and bounded download handling require
separate review.

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
| PR #78 | Flatpak local API client prototype. Add the small `flatpak_client/` layer against an injectable fake transport, with explicit loopback pinning, authentication-header handling, redirect rejection, bounded timeouts, compatibility/error mapping, and no host command execution. | Unit tests demonstrate safe request construction and failure handling without privileged host mutation. No Flatpak package or manifest exists; exact packaging scope requires separate approval. |
| PR #79 | Token pairing and first-run foundation. Add deterministic model/controller state that probes public health for reachability and validates an explicitly supplied token through the existing authenticated, read-only client contract. Add no daemon endpoint, storage backend, packaging, or graphical UI. | The six first-run states are covered offline; health alone never means paired; `401`, fail-closed `503/api_token_missing`, connection failure, and invalid responses map safely; tokens do not enter results, exceptions, logs, files, or controller state. |
| PR #80 | Diagnostics/control UI foundation. Add toolkit-agnostic, bounded UI models for daemon/pairing status, adapter readiness, preflight diagnostics, Basic/Pro presentation depth, and a disabled support-bundle affordance. Add no lifecycle control, support-bundle download/export wiring, GUI toolkit, desktop window, package, or manifest. | Offline behavior tests cover connection/authentication states, safe response projection, severity mapping, malformed-data fallback, Basic/Pro presentation fields, secret/path sanitization, output bounds, and the absence of mutation methods. |
| PR #81 | Flatpak packaging/app shell prototype (current phase). Add the first rough installable/testable Flatpak shell, JSON manifest, lazy GTK 4 placeholder window, standard-library smoke mode, and static desktop metadata. Add no production control UI, credential entry, portal export, lifecycle/configuration action, daemon or installer behavior, or privileged host integration. | Offline tests prove import without GTK, bounded safe smoke JSON, metadata/ID consistency, minimal manifest permissions and package scope, safe launcher behavior, and the absence of mutation controls. |
| Later, separately approved work | Steam Frame and VR Direct Link evidence-based research, followed by any separately approved adapter-intelligence work. | Work begins from lawful public or user-provided evidence and does not claim support before hardware, driver, regulatory, and security validation. |

Each phase is independently reviewable. A later phase is not authorized merely
because it appears in this roadmap.

## Packaging and distribution questions

The following decisions are deliberately unresolved and must be answered before
a production Flatpak release:

| Question | Required decision |
|---|---|
| Application ID | PR #81 uses `io.github.josethevrtech.VRhotspot`; confirm naming and publication ownership before a store submission. |
| Permissions | PR #81 documents its prototype finish-args; reassess network breadth, display compatibility, and any future portal before production. |
| Runtime and SDK | PR #81 selects GNOME 50 for the prototype; define update cadence, end-of-life policy, architecture targets, and reproducible/offline release expectations. |
| Desktop file | PR #81 provides a minimal launcher entry with no desktop actions or URL scheme; review production naming and startup behavior. |
| Icons and metainfo | PR #81 provides simple prototype SVG and AppStream metadata; production artwork, screenshots, releases, privacy copy, and store polish remain unresolved. Existing Web UI assets are not automatically release-ready desktop metadata. |
| Local daemon discovery | Decide how to detect an installed/compatible daemon without broad filesystem access, service-manager control, or token leakage. |
| Offline/local-first behavior | Confirm that installed control functions need no cloud service; define behavior when the internet is unavailable and distinguish that from daemon unavailability. |
| Versioning and compatibility | Define client, API, and daemon compatibility ranges, upgrade ordering, unsupported-version messaging, and rollback expectations. |
| Release process | Define source provenance, dependency review, reproducible build inputs, signing, store submission, release notes, and coordination with host-daemon releases. |
| Installation/update ownership | Keep daemon installation and privileged updates separate from Flatpak updates; document how users avoid incompatible independent versions. |

PR #81's prototype choices are not blanket approval for production packaging
or additional permissions.

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

## Explicit non-goals for PR #81

- No finished production UI or existing Web UI change. The GTK window is a
  rough display-only placeholder, not a production control surface.
- No Flathub submission, release artifact, production metadata polish,
  screenshots, signing, or distribution automation.
- No start, stop, restart, repair, configuration, passphrase, adapter-selection,
  or other mutation control.
- No support-bundle download, temporary file handling, file chooser, portal
  integration, or export implementation. The disabled model affordance is
  non-interactive and performs no request.
- No token discovery, persistent storage, keyring/portal integration, token
  issuance, rotation, or daemon-side pairing endpoint.
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

PR #81 authorizes only the isolated Flatpak packaging/app shell prototype, its
lazy GTK placeholder, static metadata, offline tests, and this plan update. It
does not claim that a finished graphical UI, credential-entry flow,
support-bundle portal export, Flathub-polished release, Steam Frame support, VR
Direct Link support, or a known-adapter registry exists. Production UI work,
credential entry, portal export, Flathub polish, Steam Frame, VR Direct Link,
adapter registry work, and all later phases remain separately approved work.
