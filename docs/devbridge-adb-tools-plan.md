# Dev Bridge managed ADB tools plan

Status: planning document only — no behavior change in this PR

Date: 2026-07-24

This document defines how VRhotspot will detect, install, and manage the
Android platform tools (`adb`) needed by the ADB Dev Bridge, so that beginners
— especially SteamOS users — never have to understand `adb` packaging, the
Android SDK, `pacman`, or read-only filesystem changes.

Target end-user experience when tools are missing:

> **ADB tools are missing.**
>
> VRhotspot can install the developer tools needed to connect to your headset.
> This is required for wireless APK install, logcat, and Unity/Godot testing.
>
> **[Install Dev Bridge Tools]**

## Scope and non-goals

In scope for the implementation PRs this plan drives:

- A VRhotspot-managed Android platform-tools installation under
  `/var/lib/vr-hotspot/devtools/`.
- Detection/status, install, and remove flows exposed through the daemon API,
  the `vr-hotspot` CLI, the Web Portal, and (transitively) the Flatpak
  companion shell.
- An optional installer prompt and flag.
- Provenance, checksum, and cleanup rules consistent with
  `docs/VENDOR_PROVENANCE_SBOM_PLAN.md`.

Explicit non-goals (unchanged product boundaries):

- **No APK install feature and no APK downloads.**
- **No piracy features.**
- **No arbitrary `adb` command passthrough.** The daemon still never executes
  `adb`, and the new endpoints accept no free-form commands, URLs, or paths.
- **No Android authorization bypass.** VRhotspot never touches headset
  developer settings, `~/.android/adbkey*`, or the RSA authorization prompt;
  pairing codes remain placeholders exactly as in `docs/dev-bridge.md`.
- No change to the Dev Bridge read-only guarantee toward the *headset*. The
  only new mutation is host-local file management of the tools directory.

## Current state (inspected)

- `backend/vr_hotspotd/diagnostics/devbridge.py` discovers `adb` with
  `shutil.which("adb")` only (`_host_adb_path`) and reports it via
  `host_adb.{present,path}` in status, command, and readiness models. The
  `host_adb_present` readiness check warns with a distro-package hint
  (`_ADB_INSTALL_HINT`: `android-tools` on Arch/SteamOS, `adb` on
  Debian/Ubuntu, `android-tools` on Fedora) — advice a SteamOS user cannot
  follow without disabling the read-only filesystem.
- `backend/vr_hotspotd/api.py` serves `GET /v1/devbridge/{status,devices,adb,
  readiness}` and embeds the probe-free status as `vr-hotspot/devbridge.json`
  in the support bundle.
- `backend/vr_hotspotd/cli.py` is a token-authenticated, GET-only HTTP client
  with a `devbridge` subparser group (`status`, `scan`, `adb-command`,
  `logcat-command`).
- `install.sh` detects the OS (`steamos|cachyos|arch|endeavouros` → pacman,
  `ubuntu|debian|pop` → apt, `fedora` → dnf, `bazzite` → rpm-ostree), never
  modifies the immutable SteamOS base, forces the bundled networking stack on
  SteamOS/Bazzite, and records reversible host changes in an owned ledger
  (`/var/lib/vr-hotspot/firewall-rules.json`) that `uninstall.sh` replays.
- `uninstall.sh` (and its mirror `backend/scripts/uninstall.sh`) removes
  `/var/lib/vr-hotspot` and `/etc/vr-hotspot` entirely; `install.sh`
  `cleanup_previous_install` does the same before reinstalling.
- The Web Portal (`assets/index.html` / `assets/ui.js`) has **no Dev Bridge
  surface today** — it wires only preflight and the support bundle. The
  Flatpak companion's Python API client (`flatpak_client/client.py`) exposes
  the four devbridge GETs behind a method allowlist, but no companion UI uses
  them. A Dev Bridge card is greenfield UI work in both surfaces, which
  `docs/dev-bridge.md` anticipated ("Web Portal and Flatpak UI surfaces build
  on these same contracts later").
- `tests/conftest.py` hard-blocks `adb` (first entry in
  `_BLOCKED_SYSTEM_COMMANDS`) via an autouse fixture — the test harness
  itself encodes "never execute adb", which this plan preserves.
- `backend/vendor/VENDOR_MANIFEST.json` + `tools/ci/vendor_manifest_check.py`
  govern repo-vendored bytes; `docs/VENDOR_PROVENANCE_SBOM_PLAN.md` sets the
  policy rules (pinned checksums, no fetch-latest-and-trust, per-file review).
- The daemon currently makes **no outbound network requests** other than the
  optional LAN TCP probe; downloading platform-tools is a new, explicit,
  user-triggered egress and is treated as such below.

## Recommended strategy (summary)

Ship a **VRhotspot-managed platform-tools install** as the default on every
supported distro, driven by a **pinned version + SHA-256 recorded in the
repository**, installed to `/var/lib/vr-hotspot/devtools/`, performed only on
explicit user action (installer opt-in prompt, CLI `tools install`, or the Web
Portal button), verified before extraction, recorded in an owned manifest
ledger, and removed by `tools remove` and by the existing uninstall flow.
System packages remain a documented **advanced** alternative and are always
respected when present.

Rationale: one code path works identically on SteamOS (no read-only changes:
`/var/lib` is writable), Arch-family, Ubuntu, Fedora, and Bazzite; the
platform-tools zip from Google is small (~10–15 MB) and statically
self-contained; pinning + checksum keeps the supply chain reviewable per
existing vendor policy without vendoring binaries into the repo. Note:
the downloadable zip is distributed under Google's Android SDK terms —
not plain Apache-2.0 — and those terms must be reviewed before
implementation (see **Licensing** under the security model below, which
blocks the download/install feature until resolved).

## Managed ADB location

```
/var/lib/vr-hotspot/devtools/                 root:root 0755
├── platform-tools/                           extracted payload, 0755 dirs
│   ├── adb                                   0755 (world-executable)
│   └── ... (only allowlisted companion files)
├── manifest.json                             installed-tools ledger, 0644
└── .staging-<random>/                        transient; removed on success/failure
```

- Canonical managed binary: `/var/lib/vr-hotspot/devtools/platform-tools/adb`.
- Directories and `adb` are world-readable/executable so desktop users (deck,
  etc.) can run `adb` themselves; the daemon never executes it. The user's own
  `adb` invocation starts the adb server as that user, and authorization keys
  stay in the user's `~/.android/` as normal.
- `manifest.json` is an owned ledger in the spirit of the firewall ledger:
  `{"schema_version": 1, "version": "...", "source_url": "...",
  "archive_sha256": "...", "files": [{"path", "sha256", "mode"}],
  "installed_at": "..."}`. Strict validation on read; an unrecognized ledger is
  reported, never silently overwritten by `remove`.

## Pinned source of truth (repo side)

A small reviewed pin file ships in the repo, e.g.
`backend/vr_hotspotd/devtools/platform_tools_pin.json`:

```json
{
  "schema_version": 1,
  "version": "r36.0.0",
  "url": "https://dl.google.com/android/repository/platform-tools_r36.0.0-linux.zip",
  "archive_sha256": "<reviewed sha256>",
  "max_archive_bytes": 100000000,
  "arch": "x86_64",
  "extract_allowlist": ["platform-tools/adb", "platform-tools/NOTICE.txt", "..."]
}
```

- Never `platform-tools-latest-linux.zip`: the pin is a specific release with
  a specific hash, satisfying vendor-policy rule 8 (no fetch-latest-and-trust)
  and rule 7 (a version bump is a reviewed diff of version + URL + sha256).
- The binaries are **not** committed, so `VENDOR_MANIFEST.json` scope is
  unchanged; the pin file is the provenance record instead, and it is a
  candidate for a light CI check (schema, https-only host allowlist
  `dl.google.com`, well-formed sha256).
- Linux platform-tools are published for x86_64 only. On other architectures
  the tools feature reports `unsupported_arch` and points at the advanced
  system-package docs instead of attempting a download.

## ADB discovery order

`_host_adb_path()` in `devbridge.py` becomes a discovery function used by all
existing builders:

1. **Managed install**: `/var/lib/vr-hotspot/devtools/platform-tools/adb` if
   present, regular-file (not symlink), and executable → `source: "managed"`.
2. **System PATH**: `shutil.which("adb")` → `source: "system"`.
3. Neither → missing.

The `host_adb` model gains additive fields (schema_version stays 1):

```json
"host_adb": {
  "present": true,
  "path": "/var/lib/vr-hotspot/devtools/platform-tools/adb",
  "source": "managed",            // "managed" | "system" | null
  "managed": {
    "installed": true,
    "version": "r36.0.0",
    "verified": true              // ledger sha256s match on-disk bytes
  }
}
```

Managed-first is deliberate (product direction: prefer VRhotspot-managed
tools; the managed copy is the one whose bytes we can verify). A system `adb`
alone still yields `present: true` — users who installed `android-tools`
themselves are never told tools are missing.

Command generation follows discovery: when the resolved source is `system`,
generated command strings keep using bare `adb ...` (it is on PATH); when it
is `managed`, builders emit the full path
(`/var/lib/vr-hotspot/devtools/platform-tools/adb connect <IP>:5555`) so
copy-paste works with no PATH or shell-profile edits — important on SteamOS
where we refuse to write into the read-only `/usr`. No symlink is installed
into `/usr/local/bin` by default for the same reason (see open questions).

## Per-platform strategy

### SteamOS (recommended path: managed tools, zero read-only changes)

- `/var/lib` is writable on SteamOS; the managed install works with **no**
  `steamos-readonly disable`, no `pacman`, and no base-image modification —
  matching the existing SteamOS policy in `install.sh` (bundled networking
  stack, never touching the immutable base).
- Docs may mention `sudo steamos-readonly disable && sudo pacman -S
  android-tools` **only** under an "Advanced / not recommended" heading, with
  a warning that SteamOS updates wipe layered packages. The default UX never
  surfaces it.

### Arch / CachyOS / EndeavourOS

- Default: same managed install (consistent UX and a known-good version).
- If `android-tools` is already installed, discovery reports
  `source: "system"` and nothing prompts the user to install anything.
- Advanced docs: `sudo pacman -S android-tools`.

### Ubuntu / Debian / Pop!_OS and Fedora

- Default: same managed install. Distro `adb` packages can lag significantly
  (old protocol versions confuse newer headsets), so managed tools are the
  recommendation, not just the fallback.
- Advanced docs: `sudo apt install adb` / `sudo dnf install android-tools`.
- Fedora note for implementation: verify SELinux allows user execution from
  `/var/lib/vr-hotspot/devtools` (see risks).

### Bazzite (rpm-ostree)

- Managed install works unchanged (`/var/lib` is writable state on ostree
  systems) and avoids layering; consistent with the existing "do not layer
  what we can bundle" Bazzite policy in `docs/PLATFORM_COMPATIBILITY.md`.
- Advanced docs: `rpm-ostree install android-tools` with the existing honest
  reboot guidance.

## Install/remove mechanics (daemon-owned)

The daemon (root) performs the install so the Web Portal button and the CLI
share one implementation:

1. Acquire a host-local install lock (single concurrent install; second
   request gets `tools_install_busy`).
2. Download the pinned URL over HTTPS with a bounded timeout and a byte cap
   (`max_archive_bytes`) into `devtools/.staging-<random>/` (0700).
3. Hash the complete archive; **hard-fail on SHA-256 mismatch** before any
   extraction. Delete staging on every failure path.
4. Extract only `extract_allowlist` entries with explicit zip-slip protection:
   reject absolute paths, `..`, symlink/hardlink entries, unexpected file
   types, and per-file size anomalies.
5. Record `manifest.json` (per-file SHA-256 + modes), set final modes, then
   atomically move `platform-tools/` into place (install-over-install replaces
   the old tree only after the new one verifies).
6. `remove`: validate that the target is the expected non-symlinked directory
   under `/var/lib/vr-hotspot/devtools/`, delete `platform-tools/` and
   `manifest.json`, leave everything else on the host untouched. Never touches
   `~/.android`, never kills user processes (if an adb server spawned from the
   managed binary is running, report a warning with the copyable
   `.../adb kill-server` command instead).

New API surface (token-authenticated like all `/v1` routes):

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/v1/devbridge/tools` | GET | Tools status: discovery result, managed ledger state, verification, pin version, `update_available`, install job phase. |
| `/v1/devbridge/tools/install` | POST | Start the pinned install/upgrade. No request parameters that influence URL, hash, or paths. Returns `202` + job phase; progress is polled via GET. |
| `/v1/devbridge/tools/remove` | POST | Remove the managed install. No parameters. |

These are the first mutating Dev Bridge endpoints. They comply with the
existing carve-out in `docs/dev-bridge.md` ("Mutating operations, if ever
added, must be explicit and user-triggered"): both mutations are explicit
POSTs, host-local only, and the headset-facing read-only guarantee is
untouched. `docs/dev-bridge.md` and the CLI's "read-only client" description
must be amended in the implementation PR.

Install job phases reported by GET `/v1/devbridge/tools`:
`idle | downloading | verifying | installing | done | error`, with a stable
`error_code` vocabulary (`download_failed`, `checksum_mismatch`,
`unsupported_arch`, `disk_full`, `archive_invalid`, `tools_install_busy`,
`offline`, ...), each mapped to friendly UI text.

## Installer prompt behavior (`install.sh`)

- New optional step in `configure_install`, after the Flatpak companion
  prompt:

  ```
  Install Dev Bridge developer tools (adb) for headset development? (y/N)
  ```

  Interactive default **No** — keeps the base install lean and avoids a
  surprise download; the Web Portal offers the same install later with the
  product copy above.
- New flag `--install-devbridge-tools` (mirroring
  `--install-flatpak-companion`) forces the install in any mode; environment
  opt-in via `VR_HOTSPOT_INSTALL_DEVBRIDGE_TOOLS=1` for scripted installs.
- Non-interactive/CI default: skipped, like the companion.
- Implementation detail: the installer defers to the same daemon code path
  (post-daemon-start `POST /v1/devbridge/tools/install` with the freshly
  generated token, mirroring `pair_flatpak_companion_if_installed` and
  `wait_for_daemon_health`), so there is exactly one download/verify/extract
  implementation. The token must never appear in process arguments — pass it
  via a header set from the shell variable inside a heredoc'd `python3` call,
  matching the existing stdin-pipe token discipline the companion tests
  enforce.
- Failure is **never fatal** to the daemon install: warn, print the CLI
  command to retry (`vr-hotspot devbridge tools install`), continue.
- `--check-os` output remains unchanged (tools are not an OS package
  dependency on any distro), so `tools/ci/install_matrix_check.sh` is
  unaffected.

## CLI UX (`vr-hotspot devbridge tools ...`)

A new `tools` subparser group under the existing `devbridge` group, sharing
`_add_client_arguments` (`--api-url`, `--token`/`--token-stdin`, `--env-file`,
`--output`, `--timeout`):

- `vr-hotspot devbridge tools status` — GET `/v1/devbridge/tools`; prints the
  JSON status (discovery source, managed version, verification,
  `update_available`).
- `vr-hotspot devbridge tools install` — prints what will happen first
  (pinned version, exact URL, SHA-256, destination) and asks for confirmation
  on a TTY; `--yes` skips the prompt for scripts. Then POSTs install and polls
  GET until `done`/`error`, printing phase transitions.
- `vr-hotspot devbridge tools remove` — confirmation on a TTY (`--yes` to
  skip), POSTs remove, reports the result.

The CLI remains a thin HTTP client; it performs no downloads or file
operations itself. Help text and `docs/dev-bridge.md` change from "read-only
client" to "read-only except explicit, user-confirmed Dev Bridge tools
management".

## Web Portal / Flatpak UX

- The Web Portal has no Dev Bridge surface yet; the implementation adds a
  Dev Bridge card (greenfield in `assets/index.html` / `assets/ui.js`,
  served from `/var/lib/vr-hotspot/app/assets`) that renders the existing
  readiness report. Within it, the `host_adb_present` check gains a
  call-to-action state: when `host_adb.present` is false and the platform is
  supported, show exactly the product copy above with an
  **[Install Dev Bridge Tools]** button.
- The button POSTs `/v1/devbridge/tools/install` with the already-present
  session token, then polls GET `/v1/devbridge/tools` to render
  `downloading → verifying → installing → done`, then re-fetches readiness so
  the check flips to pass without a reload.
- Error states use the `error_code` vocabulary with plain-language text and a
  copyable CLI fallback command.
- When tools are installed and `update_available` is true, show a low-key
  "Update Dev Bridge Tools" action (same endpoint).
- A "Remove Dev Bridge Tools" action lives in an advanced/expander area,
  wired to the remove endpoint with a confirm dialog.
- **Flatpak companion:** the companion is a locked WebKit shell around this
  same Web Portal (per `docs/FLATPAK_ARCHITECTURE_PLAN.md`), so it inherits
  the UX with **no manifest/permission changes** — the download and all file
  writes happen in the host daemon, never inside the sandbox. This preserves
  the plan's principle that the Flatpak holds no host filesystem or command
  authority (it may not read `/var/lib/vr-hotspot` at all; the daemon API is
  its only channel).
- The companion's Python API client (`flatpak_client/client.py`) currently
  allowlists the devbridge routes as GET-only. That allowlist stays intact
  for the portal-shell path (portal JS talks to the daemon directly); it is
  extended with the two POST tools routes only if a native tray/client
  surface ever needs them — not required for this plan.

## Provenance / checksum / security model

- **Pinned, reviewed provenance.** The pin file (version, `dl.google.com`
  HTTPS URL, SHA-256, allowlist) is the reviewed source of truth; bumping it
  is a normal reviewed diff. No "latest" endpoints, no checksum fetched from
  the network, no user-supplied URLs or hashes anywhere in the API/CLI.
- **Verification before trust.** Archive SHA-256 is verified before
  extraction; per-file hashes land in the installed ledger; `tools status`
  re-verifies ledger hashes against on-disk bytes and reports
  `verified: false` (diagnostic, non-enforcing — consistent with the
  PR #75/#76 posture of reporting rather than runtime enforcement).
- **Relationship to `VENDOR_MANIFEST.json`.** Managed tools are
  downloaded-at-install, not repo-vendored bytes, so they stay outside the
  vendor manifest scope; the pin file plus installed ledger fill the
  equivalent provenance role. `vr-hotspot/devbridge.json` in the support
  bundle carries the tools status (source, version, verified) through the
  existing redaction path; no payload bytes, ever.
- **Containment.** Staging dirs are 0700 and random-suffixed; extraction is
  allowlist-only with zip-slip/symlink/size defenses; final move is atomic;
  every failure path deletes staging. The daemon never executes the
  downloaded binary — execution is always the user's own action.
- **Licensing (open blocker — must be resolved before implementation).**
  Three distinct things must not be conflated:
  - *AOSP source licensing:* the AOSP source code for `adb` and the platform
    tools is predominantly Apache-2.0.
  - *Google's downloadable SDK Platform-Tools package:* the prebuilt
    `platform-tools_*.zip` served from `dl.google.com` is distributed under
    Google's Android SDK / Platform-Tools terms, which are **not** simply
    Apache-2.0 plus a NOTICE file.
  - *What VRhotspot may do:* whether VRhotspot may automatically download,
    cache, install, or redistribute that package is governed by those terms.
    Redistribution is **not** assumed, and even the
    users-download-directly-from-Google model must be validated against the
    SDK terms.

  The Android SDK / Platform-Tools license terms must be reviewed before
  implementation. Automated download, caching, and installation are blocked
  until this license/provenance question is resolved. The implementation PR
  must record the reviewed terms, the source URL, the archive checksum, the
  version pin, and the resulting decision. Independent of the outcome, the
  extracted `NOTICE.txt` stays in the allowlist and the docs link Google's
  platform-tools page.
- **Egress honesty.** This is the daemon's first outbound internet request.
  It happens only on explicit user action, only to the single pinned host,
  and is documented in `docs/security.md` as part of the implementation PR.
- **Explicit approval of a new trust boundary.** The repo has no
  download-and-verify precedent today (all vendored bytes are git-committed
  and `enforcement_status` is inventory-only), and the vendor plan's roadmap
  boundary explicitly withholds authorization for automatic downloads in the
  driver/Steam-depot context. This plan is the explicit design + approval
  vehicle that boundary demands: a pinned, checksummed, user-triggered
  download of upstream vendor tooling (itself subject to the **Licensing**
  blocker above) — categorically different from driver redistribution or
  depot access, and reviewed as such before implementation starts.

## Uninstall cleanup model

- `uninstall.sh` and `backend/scripts/uninstall.sh` already execute
  `rm -rf /var/lib/vr-hotspot`, which removes `devtools/` — managed tools are
  cleaned up with zero new code. The
  implementation PR adds an explicit progress line ("Removing Dev Bridge
  tools...") and a best-effort informational warning if an adb server process
  launched from the managed path is still running (copyable
  `adb kill-server`; the uninstaller does not kill user processes).
- `install.sh` `cleanup_previous_install` also wipes `devtools/` on
  reinstall. Decision needed (open question below): either preserve
  `devtools/` across reinstall, or re-offer the installer prompt/flag; the
  simple default is wipe + re-offer.
- `vr-hotspot devbridge tools remove` provides removal without uninstalling
  VRhotspot.
- Nothing under any user home (`~/.android`, adb keys, headset
  authorizations) is ever created or removed by VRhotspot.

## Tests for the implementation PR

Model / discovery (extend `tests/test_devbridge_model.py`, new
`tests/test_devbridge_tools.py`):

- Discovery order: managed beats system; system used when managed absent;
  missing when neither; symlinked or non-executable managed binary rejected.
- `host_adb` additive fields present in status, adb-command, and readiness
  payloads; schema_version unchanged; command builders emit full managed path
  vs bare `adb` correctly.
- Readiness `host_adb_present` transitions: missing → warning with install
  CTA metadata; managed-installed → pass; `unsupported_arch` handling.
- Ledger parsing: valid ledger, corrupt/foreign ledger reported not trusted,
  hash mismatch → `verified: false`.

Install engine (new `tests/test_devbridge_tools_install.py`, mocked
transport — no network in tests):

- Happy path: staged download → hash verify → allowlisted extraction →
  atomic move → ledger written with correct modes/hashes.
- Checksum mismatch aborts before extraction; staging removed.
- Zip-slip (`../`, absolute paths), symlink/hardlink entries, non-allowlisted
  members, and oversize archives are rejected.
- Byte cap and timeout enforcement; disk-full and offline error codes.
- Concurrent install → `tools_install_busy`; install-over-install upgrades
  atomically.
- Remove: deletes exactly the managed tree; refuses symlinked target; no-op
  message when nothing installed.

API (extend `tests/test_api_devbridge.py`, follow `tests/test_auth.py`
conventions):

- GET `/v1/devbridge/tools` envelope/auth; POST install/remove require token;
  GET on POST-only routes rejected; no request parameter reaches URL/hash/
  path logic; job phase and `error_code` surfaces.

CLI (extend `tests/test_cli_devbridge.py`):

- `tools status|install|remove` parse and hit the right paths; install prints
  pinned URL + SHA-256 and honors confirmation/`--yes`; polling loop handles
  `done` and `error`; output/token flags behave like existing subcommands.

Installer / uninstaller (follow `tests/test_installer_endeavouros.py`,
`tests/test_installer_flatpak_companion.py` patterns):

- `--install-devbridge-tools` flag and env opt-in parsed; interactive default
  is No; non-interactive default skips; failure is non-fatal to the daemon
  install; `--check-os` output and `tools/ci/install_matrix_check.sh` matrix
  unchanged on all supported OS IDs.
- Uninstall: `devtools/` removed with `INSTALL_ROOT` (extend the
  uninstall-focused tests alongside `tests/test_firewall_uninstall_rollback.py`
  / `tests/test_flatpak_companion_cleanup.py`).

Support bundle (extend `tests/test_support_bundle_collectors.py` and
redaction tests):

- `vr-hotspot/devbridge.json` includes tools status; redaction unaffected; no
  payload bytes or absolute-home paths.

Web Portal contract (follow `tests/test_ui_support_bundle_contract.py` /
`tests/test_ui_preflight_contract.py` conventions):

- `assets/index.html` contains the Dev Bridge card ids (install button,
  status/progress message element); `assets/ui.js` exercised in the Node `vm`
  harness for the missing → installing → done transitions, `error_code`
  rendering, and escaping of API-provided strings.

Test-harness compatibility:

- All new code keeps the `tests/conftest.py` `adb` hard-block satisfied: the
  daemon and tests never execute `adb`; install-engine tests exercise only
  file/network seams (mocked), never subprocesses.

Pin file CI (optional, `tools/ci/`):

- Pin schema valid; URL host is exactly `dl.google.com` over https; sha256
  well-formed; allowlist non-empty.

## Risks

- **Privileged daemon performs a download.** Largest new surface. Mitigated
  by pin+hash, single allowlisted host, size/time caps, staging isolation,
  and no user-controlled inputs — but it is still root parsing a zip.
  Consider extracting in a privilege-dropped subprocess as hardening.
- **`dl.google.com` availability/reachability.** Offline or region-blocked
  hosts get a clean `offline`/`download_failed` state with the advanced
  system-package fallback documented; nothing else degrades.
- **x86_64-only payload.** ARM hosts must get an honest `unsupported_arch`
  status rather than a failed download.
- **SELinux on Fedora/Bazzite** may deny users executing binaries from
  `/var/lib`. Needs a real-hardware check during implementation; the fix (a
  documented file-context or a different managed root) must not regress other
  distros.
- **Pin staleness.** A too-old `adb` can mishandle new headsets; the pin
  needs an owner and a bump cadence (e.g., checked at each release).
- **Reinstall wipes managed tools** via `cleanup_previous_install`; without
  the re-offer, users silently lose tools on upgrade.
- **Messaging drift.** CLI/docs currently promise "read-only"; the
  implementation PR must update `docs/dev-bridge.md`, CLI help, and
  `docs/security.md` in the same change to keep the trust story honest.
- **User-owned adb servers.** An adb server started from a managed binary
  keeps running after remove/uninstall; we only warn (by design), which may
  surprise users.

## Open questions

1. Should reinstall preserve `devtools/` instead of wipe + re-offer? (Leaning
   preserve-if-ledger-verifies, wipe otherwise.)
2. Should install/extraction run in a privilege-dropped helper process rather
   than in the root daemon process?
3. Do we want an opt-in `PATH` convenience (shell snippet printed by the CLI,
   or a per-user symlink in `~/.local/bin`) so users can type bare `adb`?
   (Never a `/usr/local/bin` symlink — read-only on SteamOS.)
4. Exact `extract_allowlist`: `adb` + `NOTICE.txt` only, or also `fastboot`?
   Recommendation: `adb` + notices only; fastboot invites flashing-related
   support burden and is not needed for Dev Bridge.
5. Should `tools status` verification hash every file on every call (cost on
   slow storage) or cache with mtime/size heuristics?
6. Web Portal placement: inline in the readiness card vs. a dedicated "Dev
   Tools" card; needs a quick design pass against the current portal layout.
7. Does the Bazzite path need the same SELinux verification as Fedora before
   the feature is advertised there?
