# Flatpak control app architecture plan

Status: PR #93 shared Web Portal/tray companion authentication; PRs #77-#92 retained

Date: 2026-07-24

This document defines the boundary for the VRhotspot Flatpak control
application. PR #92 makes the origin-locked, daemon-served Web Portal the only
Flatpak graphical UI. Normal launch and tray primary activation open or restore
one locked WebKitGTK window; close-to-tray hides that same window. Redundant
Show and Hide menu commands are not exported. The pre-PR #92 GTK content
implementation, its token-entry flow, and every launch or failure path that
could reach it have been removed from active code. GTK remains only for the
WebKit host window, tray integration, the explicit authentication dialog,
bounded error surfaces, and small desktop utility dialogs.

PR #90's fixed 1.75x WebKit display zoom and PR #88's exact loopback origin,
navigation lock, CSP, and ephemeral session remain unchanged. If WebKit is
unavailable or construction fails, the host window shows fixed bounded error
copy; it never opens another graphical surface. The browser `/ui` route
continues to use the shared assets at normal browser scale.

PR #83's explicit terminal-only live daemon pairing smoke path and PR #84's
optional installer prompt remain. PR #93 connects the Portal and tray through a
bounded, fixed-origin, in-memory WebKit authentication bridge and the existing
companion wallet/session controller. It adds no direct host command execution,
host filesystem access, token argument or discovery path, permission, or change
to the daemon's privileged network ownership.

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

PR #81 did not include credential entry or live authenticated daemon wiring.
The shell did not discover or persist credentials through files, environment,
keyrings, portals, or daemon configuration. Support-bundle export remained a
disabled placeholder; portal export and bounded download handling still
require separate review.

## PR #82 first-run/token entry UI prototype

Historical record: PR #82 added a hidden password-style entry and a
`Connect / Validate token` action to the then-current GTK content surface.
PR #92 deleted that graphical credential flow and its shell controller
together with the retired surface. The terminal-only live smoke still uses the
existing `TokenPairingController`, loopback-only `LocalApiClient`, and
`DiagnosticsControlUiController`; there is no network call outside
`LocalApiClient`.

The live smoke token exists only in process memory for its current validation
and model-build call, and its temporary token-bearing client is discarded
afterward. The tray Authentication dialog accepts explicit user entry and can
retain it only in memory or, by explicit choice, in Secret Service. Tokens are
not written to plaintext, logged, placed in process arguments, copied into
models, or included in smoke JSON, representations, exceptions, diagnostics,
URLs, or window labels. The companion does not discover tokens from environment
variables, daemon configuration, `/etc`, `/var/lib`, or any other host
location.

The historical GTK surface rendered only the existing bounded display models:

- daemon status and safe reachability state;
- pairing accepted, rejected, unavailable, missing-daemon-token, or unknown
  state;
- daemon-provided adapter readiness summary and cards after successful
  validation;
- daemon-provided preflight summary, facts, issues, and non-interactive
  guidance after successful validation; and
- the existing disabled support-bundle affordance.

Rejected tokens never enter the result text. Connection failure renders an
offline/unreachable state, the daemon's fail-closed
`503`/`api_token_missing` response renders a missing-token state, and malformed
or unsupported responses degrade to unknown. The UI has no start, stop,
restart, repair, configuration, adapter-selection, or other mutation action.
Support-bundle portal export remains future work and its placeholder remains
disabled.

GTK loading remains lazy. Importing `flatpak_app` and running the deterministic
smoke mode still require neither PyGObject nor a token, make no API request,
and perform no credential discovery:

```bash
python -m flatpak_app --smoke-json
```

PR #82 does not change the Flatpak permissions. A developer can force a clean
rebuild, install, and run the current repository checkout with:

```bash
rm -rf .flatpak-test-build .flatpak-builder
flatpak-builder --user --install --force-clean \
  --install-deps-from=flathub .flatpak-test-build \
  packaging/flatpak/io.github.josethevrtech.VRhotspot.json
flatpak run io.github.josethevrtech.VRhotspot
rm -rf .flatpak-test-build .flatpak-builder
```

Token persistence and keyring integration remain separately reviewed future
work. Lifecycle/configuration controls, start/stop actions, support-bundle
portal export, production UI polish, Flathub polish, Steam Frame, VR Direct
Link, and adapter-registry work also remain separate future phases.

## PR #83 live daemon pairing smoke path

PR #83 adds a terminal-only developer command for exercising the installed
Flatpak against an already-running host-installed `vr-hotspotd`:

```bash
flatpak run io.github.josethevrtech.VRhotspot \
  --live-pairing-smoke-json
```

The command is explicit and opt-in. It requires an interactive terminal and
manual token entry through a hidden prompt. If standard input is not a TTY, if
hidden input is unavailable, if the prompt would fall back to echoed input, or
if the user enters no token, the command fails safely before creating a client.
The token remains in process memory only for the current validation/model-build
call and is then discarded. It is not persisted, logged, echoed, accepted as a
command-line argument, or discovered from environment variables, files,
keyrings, portals, daemon configuration, `/etc`, `/var/lib`, or any other
filesystem location.

The entered token flows through the existing `TokenPairingController` and
loopback-only `LocalApiClient`. After the daemon accepts the token, the existing
`DiagnosticsControlUiController` builds the adapter-readiness and preflight
models. There is no direct network request outside `LocalApiClient`, and the
client's loopback-only origin, proxy bypass, redirect rejection, response
bounds, and sanitized error mapping remain unchanged.

The command prints one bounded sanitized JSON object containing the application
ID, a fixed live-smoke status, daemon and pairing status models, adapter
readiness, preflight state, the disabled support-bundle affordance, and an empty
`controls.mutation_actions` list. It succeeds only when pairing is accepted and
both authenticated read-only sections produce recognized UI models. It returns
nonzero with fixed, token-free output for these safe states:

- `interactive_input_required` when standard input is not interactive;
- `token_input_empty` or `token_input_cancelled` when hidden input is not
  safely available;
- `token_rejected` for `401` or `403`;
- `daemon_unreachable` for connection failure;
- `daemon_token_missing` for the daemon's fail-closed
  `503` / `api_token_missing` result; and
- `invalid_response` for malformed, unsupported, partial, or otherwise unknown
  authenticated output.

This path does not require GTK and does not install, start, stop, restart,
repair, configure, or otherwise mutate the daemon or host. The existing
`--smoke-json` path remains deterministic, offline, token-free, and unchanged.
The Flatpak manifest permissions are unchanged.

The command requires a separately installed and running `vr-hotspotd` and the
administrator-configured token to be entered manually. Automated fake-based
tests prove the command's offline contracts, but live daemon pairing is claimed
only after a developer runs this exact installed-Flatpak command against a real
daemon and records the result. If no daemon is installed or running, live
validation is not run rather than treated as an automated-test failure.

Support-bundle portal export, token persistence and keyring storage,
lifecycle/configuration controls, production and Flathub polish, Steam Frame,
VR Direct Link, and adapter-registry work remain separately reviewed future
work.

## PR #84 optional installer companion prompt

PR #84 adds one guided question to the existing top-level installer:
`Install the Flatpak companion app?` The default is No while the companion
remains a prototype with local repository packaging. A No answer preserves the
existing daemon install path. Noninteractive installs also default to no
companion; `--install-flatpak-companion` is the only explicit unattended opt-in.

When selected, the installer checks for `flatpak` and `flatpak-builder`, resolves
the non-root user who invoked the root installer through `sudo`, and attempts a
user-scoped build/install from
`packaging/flatpak/io.github.josethevrtech.VRhotspot.json`. Build and state
directories use a private cleanup-safe temporary directory outside the tracked
tree. Builder output is captured and only a bounded tail is shown on failure.
The installer uses already available runtimes and SDKs; it does not add Flathub,
install another remote, or make a system-wide Flatpak change.

The companion is optional and best-effort. Missing tools, an unavailable GNOME
50 runtime/SDK, or another build/install failure is explained, temporary build
state is removed, and the completed daemon install remains successful. PR #84
does not add a strict companion-failure mode. The installer does not run either
Flatpak smoke command or attempt live daemon pairing.

The installer never supplies a daemon API token to the Flatpak build or app. It
does not read daemon credentials from environment variables, files, keyrings,
portals, daemon configuration, `/etc`, `/var/lib`, or another filesystem
location. Known daemon-token environment variable names are removed from the
builder process environment without inspecting their values. After install, the
Web Portal shell is the Flatpak graphical UI. The retired GTK content surface
remains absent; GTK provides only WebKit, tray, explicit authentication,
bounded-error, and small utility UI infrastructure. Tray controls require a
daemon token explicitly entered for the session or explicitly saved through
Secret Service. The companion never discovers a token from `/etc` or `/var/lib`.

Daemon uninstallers do not automatically remove the user-owned companion or any
Flatpak remote. A user may separately remove only this app with:

```bash
flatpak uninstall --user io.github.josethevrtech.VRhotspot
```

PR #91 provides the tray lifecycle/configuration controls and optional Secret
Service wallet storage. Missing or rejected credentials produce `Needs
Authentication`, distinct from unexpected `Error` failures. Support-bundle
portal export, Flathub production polish, Steam Frame, VR Direct Link, and
adapter-registry work remain separately reviewed future work.

## PR #85 retired GTK read-only surface (historical)

Historical record only: PR #92 deleted all implementation and tests described
in PRs #85-#87. PR #85 had replaced the GTK window's linear placeholder with a
GTK 4 read-only card surface. Its hidden token entry was the only graphical
credential source at that time. After the daemon accepted caller-entered text,
the app rendered the already-sanitized `DiagnosticsControlUiModel` as a
two-column card layout containing:

- daemon status;
- pairing status;
- adapter readiness, including the daemon-recommended interface, bounded card
  list, readiness labels, severities, summaries, supported bands, and reported
  reasons;
- canonical preflight readiness, severity, summary, facts, issues, and
  noninteractive display-only guidance;
- the visible but disabled support-bundle placeholder; and
- a visible controls boundary with an empty mutation-action list.

The same dashboard surface safely renders unpaired, rejected, unreachable, and
malformed-response states without copying raw response bodies or exception
text. It mirrors information available in the Web UI only at a safe read-only
level; it does not copy Web UI implementation code or attempt full visual
parity. Responsive refinement, accessibility review, theme polish, and full
visual parity remain future work.

PR #85 adds no refresh action. The dashboard is populated by the existing
validation/model-build call, so the temporary token-bearing client is still
discarded after that call. Any future refresh behavior must be separately
reviewed and must use only the authenticated read-only
`TokenPairingController` / `LocalApiClient` /
`DiagnosticsControlUiController` path without token persistence or direct
networking.

The support-bundle button remains insensitive and performs no daemon request,
download, filesystem write, chooser, or portal operation. The controls card
contains no buttons and no callable lifecycle or configuration action. Token
persistence and keyring storage, lifecycle/configuration controls,
support-bundle portal export, and full visual polish all remain future work.
The `--smoke-json` and `--live-pairing-smoke-json` command contracts remain
unchanged, GTK loading remains lazy, and the Flatpak manifest permissions are
not expanded.

## PR #86 installed PasswordEntry compatibility hotfix

PR #86 preserves PR #85's behavior while making placeholder setup compatible
with the GTK 4 `PasswordEntry` binding shipped by the installed GNOME runtime.
The historical GTK surface preferred the direct placeholder setter when available and
falls back to the supported GObject property API. If neither form is available,
the optional placeholder is skipped instead of crashing activation.

The widget remains a password-style entry, the peek affordance remains
intentional, and the entry is still cleared before validation. The hotfix adds
no token source, retention, storage, logging, model field, daemon call,
permission, or mutation control.

## PR #87 retired GTK surface parity pass (historical)

PR #87 uses the existing Web Portal only as a design and product reference for
the retired GTK card surface. It adopted the Portal's safe hierarchy and terminology
where they describe the same daemon-provided read-only information:

- a clearer application header and connection/pairing area;
- an at-a-glance readiness and recommended-adapter summary;
- emphasized daemon-recommended adapter cards with readiness details and top
  reasons;
- canonical preflight diagnostics organized as Readiness & Host Summary,
  Facts, Blocking Issues, Warnings, Other Issues when required, and Recommended
  Actions; and
- explicitly unavailable Support Bundle and Controls Boundary cards.

Every `ok`, `warning`, `blocked`, `error`, and `unknown` state is rendered with
a consistent uppercase severity badge. Paired state and the daemon-recommended
adapter receive separate, visible emphasis. Disabled sections use honest
unavailable copy and insensitive widgets rather than appearing broken. The
layout remains scrollable and uses GTK frames, boxes, grids, labels,
buttons, and one small bounded GTK stylesheet for spacing, badge shape, and
card emphasis.

This is visual and product parity, not implementation sharing. The Flatpak does
not load, embed, copy, or execute the Portal, does not add a WebView, and does
not package Portal JavaScript, Portal stylesheets, browser engines, or other
frontend runtime dependencies. The existing Web UI behavior and files remain
unchanged.

The read-only boundary remains unchanged. The only enabled action validates a
caller-entered token through the existing loopback-only client/controller path.
The hidden GTK entry is cleared before validation, and the token is retained
only for the current in-memory validation/model-build callback. No refresh
control is added because there is no retained authenticated session.

Support-bundle export remains a disabled placeholder and performs no daemon
request, download, filesystem write, chooser, or desktop export operation. The
controls boundary remains unavailable with no lifecycle, configuration,
start, stop, restart, or repair action. Token persistence/keyring integration,
lifecycle/configuration controls, support-bundle desktop export, and production
packaging polish remain separately reviewed future work.

## PR #88 locked Web Portal shell spike

PR #88 adds the explicit `--web-portal-shell` runtime flag. It does not accept a
URL argument. The flag loads the daemon-served Portal directly from
`http://127.0.0.1:8732/ui`; the daemon's public `/` route redirects to that same
page and `/assets/*` serves its existing CSS, JavaScript, images, and bundled
frontend libraries. No Portal asset is copied into the Flatpak.

GNOME Platform 50 supplies the GTK 4 WebKitGTK binding as the `WebKit` GI
namespace version `6.0`. The shell imports it lazily and creates a
`WebKit.NetworkSession.new_ephemeral()` session. Persistent HTTP credential
storage, cookies, page cache, offline application cache, DNS prefetching,
back/forward gestures, automatic JavaScript windows, and file-URL access are
disabled. The portal's own manual token-entry behavior remains inside the
daemon-served frontend and any Web Storage it uses is confined to the ephemeral
WebKit session rather than persisted by the Flatpak shell.

The WebView is locked to the exact literal HTTP origin
`http://127.0.0.1:8732`. Navigation and response policy decisions fail closed
unless their URI has that exact scheme, host, and port. New-window navigation is
always denied, and context menus and WebKit permission requests are denied.
An additive default Content Security Policy permits documents, subresources,
forms, and API connections only on the pinned daemon origin; external sites and
remote subresources are not permitted. There is no address bar, arbitrary URL
option, popup window, or general-browser mode.

The shell does not place an API token in a URL, header, request override,
JavaScript, WebKit user script, Local Storage, Session Storage, cookie, model,
exception, representation, or smoke JSON. It does not read a daemon token from
the environment, daemon configuration, files, keyrings, portals, `/etc`,
`/var/lib`, or another filesystem location. Token entry, validation, and use
remain the existing Web Portal's behavior. PR #92 removed the separate GTK
manual-entry path.

If the `WebKit` 6.0 namespace or required ephemeral-session API is unavailable,
or WebKit construction fails during activation, PR #92 populates the host
window with fixed bounded GTK error copy and no alternate interface. If the
fixed local Portal load fails, the shell displays a bounded token-free
local-daemon error with a retry button that reloads only the same fixed URL.

The manifest continues to use only its existing network, IPC, Wayland, and
fallback X11 finish arguments. WebKitGTK is supplied by the selected GNOME 50
runtime, so PR #88 adds neither a separately downloaded dependency nor a
permission, filesystem grant, bus grant, device grant, or copied browser/UI
payload. The Flatpak remains a companion shell; `vr-hotspotd` remains the
privileged authority and still serves the Portal and owns all API,
authentication, lifecycle, configuration, networking, and host mutation
behavior. PR #88 does not switch the default launch mode.

## PR #89 Web Portal shell layout and theme polish

PR #89 changes the shared Web Portal stylesheet rather than adding a Flatpak-only
frontend. The daemon continues to serve `assets/index.html`, `assets/ui.css`,
and `assets/ui.js` at `/ui` and `/assets/*`, while the Flatpak WebKit shell
continues to load that exact daemon-served page. The browser Portal and the
Flatpak shell therefore receive the same layout, colors, controls, and product
identity from one source of truth.

Basic mode now uses the available viewport more effectively: its container can
grow substantially beyond the previous narrow desktop cap, its two primary
cards share medium-width rows, Adapter Readiness spans the row at those widths,
and wide desktops use three flexible columns. A narrow breakpoint returns the
cards to one readable column. These rules are scoped to Basic mode, so Pro
mode's sidebar, tabs, content layout, controls, and behavior remain unchanged.

The shared form-control theme now opts into the dark color scheme and gives
inputs, textareas, selects, option rows, focus states, and disabled states
explicit readable colors. The select arrow uses a local CSS treatment because
WebKitGTK may otherwise retain a bright native select face despite the page's
dark palette. This compatibility styling is bounded to form controls and adds
no JavaScript, downloaded dependency, control-semantic change, or API change.
In particular, the Basic-mode USB Wi-Fi Adapter select remains the same
`ap_adapter` control and continues through the existing portal behavior.

PR #89 did not change the Flatpak entry point, WebView construction, exact
origin/navigation policy, Content Security Policy, token behavior, then-current
GTK surface, manifest permissions, daemon behavior, or installer. PR #92 later
made the locked Web Portal shell the default while preserving those WebKit
security boundaries.

## PR #90 Flatpak Web Portal shell display scaling/density polish

PR #90 sets the locked WebKit WebView to a fixed 1.75x zoom. The value is
clamped to the reviewed 1.0x-2.0x app-shell range and is not accepted from the
CLI, URL, query string, configuration, or environment. A 1.75x value provides a
substantial desktop-app density increase in the 1200x900 shell without reducing
the effective viewport as aggressively as 2.0x.

The scaling is a WebKit presentation property on the Flatpak
`--web-portal-shell` WebView. The browser Portal remains backed by the same
shared visual assets but is not forced into the Flatpak shell scale. PR #90
does not change shared CSS, Web UI JavaScript, daemon-served routes, or browser
behavior.

The exact local daemon URL pinning, external and new-window navigation blocking,
ephemeral WebKit network session, token boundaries, and manifest permissions
remain unchanged in PR #92. The 1.75x scale now applies to the only Flatpak
graphical shell.

## PR #91 system-tray control surface and desktop identity

PR #91 makes the optional Flatpak companion a persistent desktop utility while
keeping `vr-hotspotd` as the sole privileged host-mutation boundary. An explicit
`--tray` launch mode owns a StatusNotifierItem and DBusMenu. PR #92 changes its
lifecycle-owned window to the locked Web Portal shell: it is shown initially,
restored on primary tray activation, and hidden by close-to-tray. Repeated
activation reuses the same window, so redundant Show and Hide menu commands are
not exported. `Quit VR Hotspot` exits only the companion; it does not issue a
hotspot stop request. If the tray backend cannot register, the Web Portal
window still opens and closes normally instead of crashing or becoming hidden
without a reachable tray icon.

The GNOME 50 runtime provides Gio/GDBus and libsecret but does not provide an
AppIndicator/Ayatana binding. The tray therefore implements the standard
StatusNotifierItem and `com.canonical.dbusmenu` interfaces with lazy Gio
imports. KDE Plasma consumes those interfaces directly. Menu construction and
state sensitivity live in a toolkit-independent model, separate from the
authenticated API controller and desktop backend. Mutation requests are
serialized, repeat activation is rejected while work is pending, and a
successful operation refreshes status and configuration before the controls
are re-enabled.

The tray uses only these fixed daemon operations:

- `GET /v1/status` for live phase and running state
- `GET /v1/config` for `enable_internet` and `autostart`
- existing authenticated `POST /v1/start`, `/v1/stop`, `/v1/restart`, and
  `/v1/repair` lifecycle operations
- existing authenticated `POST /v1/config` with the strict
  `enable_internet` field for the tray label `Share Internet Connection`
- new authenticated `POST /v1/autostart` with exactly one Boolean `enabled`
  field for `Start Hotspot Automatically`

Hotspot boot autostart remains the existing coordinated
`config["autostart"]` plus `vr-hotspot-autostart.service` behavior. The narrow
autostart endpoint invokes a fixed enable or disable operation for that
existing unit and updates the same canonical config value; it is not a second
startup system or a generic service/config mutation endpoint.

`Launch VR Hotspot at login` is a distinct desktop-session concern. PR #91
does not implement that toggle: the Background portal can request background
or autostart behavior but does not provide a dependable current-state query
that could keep a tray checkbox honest across Plasma and other desktops.
Creating a competing home-file mechanism would also broaden the current
Flatpak lifecycle. A future implementation must control only this app's entry
and must not be labeled as hotspot boot autostart.

Privacy Mode remains a companion-local display choice matching the Portal's
existing local preference. It controls whether a tray status refresh requests
bounded daemon logs; it is not presented as daemon configuration. The tray
currently defaults to privacy enabled and does not persist that preference.

The Authentication dialog accepts only explicit user entry. It can test the
credential, explicitly reveal or copy it, replace it, clear it, or save it
through a stable libsecret schema:
`io.github.josethevrtech.VRhotspot.ApiToken`, with app-specific
`application` and `credential` attributes. Normal retrieval is silent only
after the user opted to save. Secret Service can be backed by KDE Wallet or
another desktop provider. When no provider is available, the token remains
memory-only and the UI reports that secure persistence is unavailable. The
Flatpak never searches `/etc/vr-hotspot/env`, `/var/lib/vr-hotspot`, command
arguments, URLs, query strings, or plaintext files for a token, and clearing
removes only the matching VR Hotspot wallet item.

The installed scalable application icon now uses a cyan VR mark on a near-black
rounded tile, with running, working, and error variants that retain the same
base identity at panel sizes. Packaging installs all variants through the
existing application-ID icon path.

The only new session-bus grants are:

- `--talk-name=org.kde.StatusNotifierWatcher` to register the standard tray item
- `--talk-name=org.freedesktop.secrets` for explicit Secret Service storage

PR #91 adds no system-bus, device, host/home filesystem, broad D-Bus, or host
command permission. The app retains ordinary `--share=network` only for its
fixed authenticated loopback API origin. Notifications contain fixed bounded
operation summaries and never include credentials or raw daemon errors.

`--smoke-json`, `--live-pairing-smoke-json`, and
`--web-portal-shell` remain compatible. The last is now an alias for the
default graphical behavior. The Web Portal shell retains its 1.75x zoom,
ephemeral session, exact loopback origin, CSP, and navigation lock.

Tray status distinguishes `Running`, `Stopped`, `Transitioning`,
`Needs Authentication`, `Daemon Unavailable`, and `Error`. Missing, rejected,
or daemon-missing credentials map to `Needs Authentication`; connection
failure maps to `Daemon Unavailable`; only unexpected or malformed failures
map to `Error`. The saved wallet/session state and daemon status are resolved
before the tray item is registered, so the initial icon, tooltip, and command
sensitivity use the authenticated live state. Authentication remains enabled
while credentials are needed. Authenticated `Stopped` enables Start, Restart,
and Repair; authenticated `Running` enables Stop, Restart, and Repair.
Transitioning disables conflicting mutations and alone requests the working
attention state. Needs Authentication and Stopped remain static. Saving or
testing an explicitly entered token triggers a serialized status refresh.

The exported menu is deterministic and grouped without duplicating submenu
actions at the top level. Current status remains at the top. **Hotspot
Commands** contains Start, Stop, Restart, and Repair; **Network** contains Share
Internet Connection; and **Advanced** contains Authentication, Refresh Status,
Open Diagnostics, Privacy Mode, and Start Hotspot Automatically. Quit remains
top-level and exits only the companion. Start Hotspot Automatically controls
daemon/hotspot boot autostart, not desktop-companion login autostart. Launch at
Logon remains deferred and no tray item is exported for it. Primary activation
is the only tray action that opens or restores the already-default graphical
shell.

## PR #93 shared Portal and tray authentication state

PR #93 treats the Web Portal shell and tray as two surfaces of one companion
process, not as separate authentication clients. Both use the existing
`AuthenticationController`. An explicitly entered token is still accepted only
after the Portal's authenticated status request succeeds. The origin-locked
WebKit page then sends one bounded, versioned message to the host; the host
adopts the token in memory, stores it in the app-specific Secret Service wallet
when that provider is available, and requests an immediate tray refresh.

The reverse path is equally narrow. On launch, the existing authentication
controller may retrieve the matching app-specific wallet item. The Portal can
request that current wallet/session token only through WebKit's in-memory
script-message reply mechanism. The credential is not placed in a URL, query
string, command argument, local file, log, notification, menu label, smoke JSON,
or exception. The bridge accepts only fixed protocol message shapes, enforces
token and message-size limits, and responds only while the WebView is at the
exact loopback origin used by `/ui` and its same-origin assets.

Portal logout and token clearing clear the companion's session and matching
wallet item and refresh the tray. Clearing through the tray authentication
dialog also clears the Portal's in-memory session. If Secret Service is
unavailable, an accepted Portal token remains usable only in the current
companion process; it is not persisted elsewhere.

The companion never invokes sudo or searches `/etc/vr-hotspot/env`,
`/var/lib/vr-hotspot`, daemon configuration, environment variables, or command
arguments for credentials. PR #93 adds no filesystem, host/home, device,
system-bus, or other Flatpak permission. The exact WebKit origin and navigation
lock, CSP, ephemeral network session, and 1.75x zoom remain unchanged.

Tray status and icon policy is explicit. Missing or rejected credentials map to
`Needs Authentication`, not `Error`; the authentication action stays enabled
and daemon controls stay disabled. Daemon connection failure maps to
`Daemon Unavailable`. An authenticated running hotspot maps to `Running`, and
an authenticated stopped hotspot maps to `Stopped`, with controls enabled
according to those states. `Needs Authentication` is static. The working
attention/pulsing state is used only for `Transitioning` while Start, Stop,
Restart, Repair, or another serialized operation is active.

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
| PR #81 | Flatpak packaging/app shell prototype. Add the first rough installable/testable Flatpak shell, JSON manifest, lazy GTK 4 placeholder window, standard-library smoke mode, and static desktop metadata. Add no production control UI, credential entry, portal export, lifecycle/configuration action, daemon or installer behavior, or privileged host integration. | Offline tests prove import without GTK, bounded safe smoke JSON, metadata/ID consistency, minimal manifest permissions and package scope, safe launcher behavior, and the absence of mutation controls. |
| PR #82 | First-run/token entry UI prototype. Add a hidden GTK token entry, an explicit validation action, and shell-level orchestration through the existing local client, pairing controller, and diagnostics UI controller. Keep tokens in memory only, keep GTK optional for tests, and add no persistence, mutation, portal, daemon, installer, or permission behavior. | Offline tests cover accepted, rejected, unreachable, missing-daemon-token, and malformed outcomes; safe display model updates; token non-disclosure/non-persistence; unchanged smoke behavior; static Flatpak packaging; and the absence of discovery or mutation controls. |
| PR #83 | Live daemon pairing smoke path. Add an explicit terminal-only installed-Flatpak command with strict hidden manual token entry and bounded sanitized JSON assembled through the existing pairing, local-client, and diagnostics UI boundaries. Add no token argument or discovery/storage path, mutation control, daemon/installer behavior, or permission. | Offline tests cover TTY and hidden-input refusal, success, token rejection, daemon unreachability, missing daemon token, malformed data, output bounds and redaction, unchanged offline/GUI behavior, and the absence of discovery or mutation controls. A real-daemon run of the documented command is required before live pairing is claimed. |
| PR #84 | Optional installer companion prompt. Add a default-No guided choice and an explicit `--install-flatpak-companion` unattended opt-in for a best-effort user-scoped local build/install. Add no remote configuration, token transfer/storage, smoke execution, daemon/runtime behavior, uninstaller mutation, or permission change. | Deterministic installer tests cover guided No/Yes, unattended default/opt-in, missing tools, bounded build failure, cleanup, credential-environment scrubbing, unchanged manifest permissions, and non-destructive uninstall boundaries. Existing Flatpak tests and real packaging validation remain required. |
| PR #85 | Historical GTK read-only card surface, removed by PR #92. | Historical tests covered bounded rendering and secret-safe explicit entry; PR #92 deletes that implementation and those view tests. |
| PR #86 | Historical installed `PasswordEntry` compatibility hotfix, removed by PR #92. | The password field and compatibility helper no longer exist in active shell code. |
| PR #87 | Historical GTK/Web Portal visual-parity pass, removed by PR #92. | The removed surface no longer competes with the daemon-served Portal. |
| PR #88 | Locked Web Portal shell spike. Add an explicit WebKitGTK 6.0 mode loading only `http://127.0.0.1:8732/ui` through an ephemeral, origin-locked WebView. | Deterministic tests pin the exact origin, deny external/new-window navigation, prove ephemeral construction and no injection, and retain bounded load-failure handling. |
| PR #89 | Web Portal shell layout and theme polish. Improve shared Portal CSS across viewport sizes and WebKitGTK form controls. | Asset tests prove responsive layout, dark control states, retained Basic/Pro behavior, and one daemon-served asset source for browser and Flatpak. |
| PR #90 | Flatpak Web Portal shell scaling/density polish. Apply fixed bounded 1.75x WebKit zoom to the locked shell. | Tests prove fixed zoom/clamping, no user-controlled scale, normal browser scale, and unchanged origin/session/token boundaries. |
| PR #91 | Flatpak system-tray control surface and desktop identity. Add StatusNotifierItem/DBusMenu, close-to-tray, typed live state, fixed controls, explicit Secret Service authentication, and cyan/black icons. | Tests prove complete menu/state mapping, serialized mutations, refresh-after-success, companion-only quit, safe wallet behavior, narrow D-Bus grants, and installed icon identity. |
| PR #92 | Web Portal-only Flatpak graphical UI. Delete the retired GTK content surface and all routes/tests supporting it; make default and tray window behavior use one locked Web Portal shell; classify authentication and daemon availability separately; organize the tray menu. | Tests prove default/alias/tray activation, close-to-tray and single-window behavior, bounded WebKit errors with no alternate surface, retained origin/CSP/session/zoom locks, six status classes, explicit-auth refresh, deterministic menu sections, no credential leakage, unchanged permissions, and zero forbidden active references. |
| PR #93 | Shared Web Portal/tray companion authentication. Connect accepted Portal entry and logout to the existing wallet/in-memory session controller, supply an existing wallet token only through a bounded fixed-origin WebKit reply, refresh tray state immediately, and reserve working attention for transitions. | Tests prove bidirectional sync, wallet and current-process fallback behavior, strict message/origin bounds, clear synchronization, token secrecy, correct auth/running/unavailable state and menu mapping, static auth-needed indication, retained WebKit locks, unchanged permissions, and no native dashboard. |
| Later, separately approved work | Steam Frame and VR Direct Link evidence-based research, followed by any separately approved adapter-intelligence work. | Work begins from lawful public or user-provided evidence and does not claim support before hardware, driver, regulatory, and security validation. |

Each phase is independently reviewable. A later phase is not authorized merely
because it appears in this roadmap.

## Packaging and distribution questions

The following decisions are deliberately unresolved and must be answered before
a production Flatpak release:

| Question | Required decision |
|---|---|
| Application ID | PR #81 uses `io.github.josethevrtech.VRhotspot`; confirm naming and publication ownership before a store submission. |
| Permissions | PR #91 adds only exact session-bus talk grants for `org.kde.StatusNotifierWatcher` and `org.freedesktop.secrets`; reassess network breadth, display compatibility, and any future portal before production. |
| Runtime and SDK | PR #81 selects GNOME 50 for the prototype; define update cadence, end-of-life policy, architecture targets, and reproducible/offline release expectations. |
| Desktop file | PR #91 launches explicit tray mode with no URL scheme; desktop-login autostart is not implemented. Review production startup policy separately. |
| Icons and metainfo | PR #91 installs cyan/black scalable base and state icons plus updated AppStream tray copy; screenshots, release process, privacy copy, and store polish remain unresolved. |
| Local daemon discovery | Decide how to detect an installed/compatible daemon without broad filesystem access, service-manager control, or token leakage. |
| Offline/local-first behavior | Confirm that installed control functions need no cloud service; define behavior when the internet is unavailable and distinguish that from daemon unavailability. |
| Versioning and compatibility | Define client, API, and daemon compatibility ranges, upgrade ordering, unsupported-version messaging, and rollback expectations. |
| Release process | Define source provenance, dependency review, reproducible build inputs, signing, store submission, release notes, and coordination with host-daemon releases. |
| Installation/update ownership | Keep daemon installation and privileged updates separate from Flatpak updates; document how users avoid incompatible independent versions. |

PR #88-#91's shell, tray, and presentation choices are not blanket approval for
production packaging or additional permissions.

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

## Explicit non-goals for PR #93

- No second graphical shell, alternate failure UI, legacy launch route, or
  development-only route for the removed GTK content surface.
- No Web UI route, CSS, CSP, origin, navigation, ephemeral-session, or zoom
  change outside the narrow companion authentication bridge.
- No arbitrary URL argument, address bar, external/new-window navigation,
  remote-site load, or general browser.
- No token CLI argument, discovery endpoint, direct `/etc` or `/var/lib`
  secret read, plaintext credential file, or automatic copy/reveal.
- No desktop-login companion autostart. It remains distinct from existing
  hotspot boot autostart and requires a separately reviewed reliable mechanism.
- No generic daemon configuration or systemd-control API. The new endpoint is
  limited to the existing hotspot-autostart unit and Boolean config setting.
- No Flatpak command execution for daemon controls and no direct access to
  systemctl, NetworkManager, iwd, hostapd, dnsmasq, firewall, routing,
  interfaces, devices, namespaces, or other privileged host mutation.
- No broad host/home filesystem, system-bus, device, or wildcard D-Bus access.
- No installer redesign, uninstaller behavior change, vendor file/manifest
  change, support-bundle export, Flathub release automation, screenshots, or
  signing.
- No Steam Frame, VR Direct Link, known-adapter registry, or
  HostFactsSnapshot work.

PR #93 authorizes only the fixed-origin WebKit authentication bridge, reuse of
the existing companion wallet/session state, Portal/tray clear synchronization,
tray status and attention-icon correction, deterministic tests, and
documentation above. It does not change the fixed autostart API, session-bus
grants, desktop identity, WebKit security boundaries, or permissions. A real
daemon pairing or live lifecycle result is claimed only when exercised with an
explicitly entered or previously saved credential; no credential is printed or
exported in validation.
