# Dev Bridge Platform-Tools license and provenance decision

Status: decision document — resolves the path-selection part of the licensing
blocker in `docs/devbridge-adb-tools-plan.md`; no behavior change in this PR

Date: 2026-07-25

`docs/devbridge-adb-tools-plan.md` blocks the managed ADB tools feature until
the Android SDK / Platform-Tools license question is resolved. This document
decides the implementation path, defines the license-acceptance and
provenance conditions that path must satisfy, and enumerates what remains
blocked. It is governed by the policy rules in
`docs/VENDOR_PROVENANCE_SBOM_PLAN.md` (in particular rules 3, 4, and 8: an
unclear license is a blocked status, not an inference of permission; no
implicit binary sources; no fetch-latest-and-trust).

## Licensing facts this decision rests on

Three distinct things must not be conflated (mirroring the plan's Licensing
section):

1. **AOSP source licensing.** The AOSP source code for `adb` and the platform
   tools is predominantly Apache-2.0. This is why Linux distributions can
   build and redistribute their own `android-tools` packages from source
   under their own packaging review.
2. **Google's downloadable SDK Platform-Tools package.** The prebuilt
   `platform-tools_*.zip` served from `dl.google.com` is distributed under
   the Android SDK License Agreement
   (<https://developer.android.com/studio/terms>), which is **not** simply
   Apache-2.0 plus a NOTICE file. Google's own download page for SDK
   Platform-Tools gates the direct download links behind an explicit
   agreement to those terms, and Google's documentation recommends the
   Android Studio SDK Manager or the `sdkmanager` command-line tool (which
   itself requires explicit license acceptance) as the normal installation
   paths.
3. **What VRhotspot may do.** Whether VRhotspot may download, cache, install,
   or redistribute that package is governed by those terms. VRhotspot does
   **not** assume it may redistribute Platform-Tools, and VRhotspot must
   **not** silently bypass Google's license-acceptance step. Distro-built
   `android-tools` packages are the distro's build of the AOSP source, not
   Google's zip; their existence proves nothing about the zip's terms.

The safe and widely used model consistent with these facts: the **user**
accepts Google's terms and the software acts only as the user's agent,
downloading the official package directly from Google to that user's own
machine, on that user's explicit instruction, with the terms shown and
affirmatively accepted first. VRhotspot itself never becomes a distributor
of the bytes.

## Options considered (ranked)

### Option B — user-driven download from Google with explicit license notice and acceptance (RECOMMENDED)

VRhotspot downloads the pinned official `platform-tools` zip directly from
`dl.google.com` to the user's machine, but only after the user has been shown
a license notice naming the Android SDK License Agreement (with its URL) and
has explicitly accepted it. No redistribution occurs: the bytes travel once,
from Google to the accepting user's host.

- Pros: preserves the beginner-friendly SteamOS UX ("VRhotspot can install
  Dev Bridge Tools") with zero read-only-filesystem changes; one code path on
  every supported distro; pinned version + SHA-256 keeps the supply chain
  reviewable; the acceptance model matches how Google itself gates the same
  download (page checkbox, `sdkmanager --licenses`).
- Cons: VRhotspot must be scrupulous about not bypassing the terms — the
  acceptance must be explicit, recorded, and never defaulted; the daemon
  gains its first outbound internet request; a residual maintainer review of
  the current terms text is still required before implementation (see
  "What remains blocked").
- Verdict: **selected**, subject to the conditions in this document.

### Option A — distro packages only (retained as the always-respected alternative and documented advanced path)

Rely on `android-tools` / `adb` from the host package manager.

- Pros: the distro handles licensing entirely (distro builds from AOSP
  source); zero new egress or download code in VRhotspot.
- Cons: fails the primary product goal on SteamOS — `pacman -S
  android-tools` requires `steamos-readonly disable` and is wiped by SteamOS
  updates, which is exactly the beginner trap the plan exists to avoid;
  distro `adb` can lag badly on Debian/Ubuntu.
- Verdict: **not the default, never removed.** Discovery must always honor a
  system `adb` (`source: "system"` — a user who installed `android-tools`
  themselves is never told tools are missing and is never prompted to
  install anything), and the per-distro package commands stay documented
  under an "Advanced" heading, exactly as the plan already specifies.

### Option C — use `sdkmanager` if already installed (not implemented as a driven path)

- Pros: Google's officially recommended installation path with its own
  built-in license-acceptance flow.
- Cons: not beginner-friendly (requires the Android command-line tools and a
  Java runtime, i.e. more Android SDK tooling than `adb` itself); driving
  another tool's interactive license flow from the daemon would be fragile
  and risks *becoming* a silent bypass if automated with `--licenses`
  pre-acceptance on the user's behalf.
- Verdict: **not implemented.** Its useful outcome is already covered: an
  `adb` installed via `sdkmanager` and present on `PATH` (or reachable by
  the user) is honored through the existing `system` discovery path. Docs
  may mention `sdkmanager` as another advanced alternative; VRhotspot never
  invokes it.

### Option D — vendor Platform-Tools binaries in the repository (REJECTED)

- Committing Google's prebuilt binaries to the repo, or shipping them in
  releases, the Flatpak, or any VRhotspot-served artifact, is
  redistribution of the SDK package. VRhotspot has no established right to
  do that, and under vendor-policy rule 3 an unknown/unclear redistribution
  status is **blocked**, not inferred as permitted.
- It would also expand `VENDOR_MANIFEST.json` scope with binaries whose
  provenance review burden the project is explicitly trying not to grow.
- Verdict: **rejected.** Revisit only if Google's terms are someday
  affirmatively shown to permit this exact form of redistribution, reviewed
  and recorded per the vendor policy.

Ranking: **B > A > C > D.** B is the default UX; A is permanently respected
and documented; C is subsumed by A's discovery; D is rejected.

## What VRhotspot may download, cache, and install

Under Option B, after explicit user acceptance, VRhotspot may:

- Download the **single pinned** `platform-tools_r<version>-linux.zip` from
  `https://dl.google.com/...` (exact URL from the reviewed pin file) over
  HTTPS, with the plan's byte cap and timeout, into the 0700 staging
  directory. Only on explicit user action; never pre-fetched, never in the
  background, never auto-updated.
- Verify the archive against the pinned SHA-256 **before** any extraction.
- Extract only the allowlisted files (`platform-tools/adb`, `NOTICE.txt`,
  and any other reviewed notice files — the notice files are mandatory in
  the allowlist, not optional) into
  `/var/lib/vr-hotspot/devtools/platform-tools/`.
- Record the installed-tools ledger (`manifest.json`) including the
  acceptance metadata defined below.
- **Delete the downloaded archive after extraction.** No archive cache is
  retained; the only persisted artifacts are the extracted allowlisted files
  and the ledger on that one machine. This keeps VRhotspot's footprint
  unambiguously "the user's installed copy", not a cached redistributable.

## What VRhotspot must never redistribute or do

- Never commit the zip, `adb`, or any other Platform-Tools payload bytes to
  the repository, releases, tags, or CI artifacts.
- Never mirror, re-host, proxy, or re-serve the archive or extracted
  binaries — not via the daemon API, the Web Portal, the support bundle, the
  Flatpak companion, or any future Steam depot or package. The daemon API
  reports tools *status* only; it never serves tools *bytes*.
- Never embed Platform-Tools in the Flatpak companion or any installer
  payload.
- Never download without a recorded, explicit user acceptance; never treat a
  generic "yes to everything" answer as license acceptance (see UX below).
- Never fetch `platform-tools-latest-linux.zip` or any unpinned/"latest"
  endpoint (vendor-policy rule 8).
- Never accept Google's terms *for* the user, pre-check an acceptance box,
  or auto-answer another tool's license prompt.

## License notice and acceptance UX

All three surfaces present the **same substance** before any download:

- the name of the agreement ("Android SDK License Agreement") and its URL
  (`https://developer.android.com/studio/terms`);
- what will be downloaded: pinned version, exact `dl.google.com` URL, and
  SHA-256;
- where it will be installed (`/var/lib/vr-hotspot/devtools/`);
- a statement that the tools are Google's software provided under Google's
  terms, downloaded directly from Google, and not distributed by VRhotspot.

**Explicit confirmation is required: yes, always.** The user must
affirmatively accept the license before the first install; acceptance is
never the default and never implied by an unrelated confirmation.

Per surface:

- **Installer (`install.sh`).** The optional prompt from the plan becomes a
  two-part step: print the notice block, then ask
  `Install Dev Bridge developer tools (adb)? This requires accepting the
  Android SDK License Agreement (y/N)`. Default **No**. Non-interactive/CI
  runs skip by default. The scripted opt-ins
  (`--install-devbridge-tools`, `VR_HOTSPOT_INSTALL_DEVBRIDGE_TOOLS=1`) are
  documented as constituting acceptance of the Android SDK License
  Agreement, and the installer still prints the notice (including the terms
  URL) before proceeding. Failure stays non-fatal to the daemon install.
- **CLI (`vr-hotspot devbridge tools install`).** Prints the notice (URL,
  version, SHA-256, destination, terms link), then asks for acceptance on a
  TTY. The generic `--yes` flag does **not** imply license acceptance; a
  dedicated `--accept-android-sdk-license` flag is required for
  non-interactive use (mirroring the `sdkmanager --licenses` convention).
  Without a TTY and without that flag, the command fails with a message
  pointing at the flag and the terms URL.
- **Web Portal (and transitively the Flatpak companion shell).** The
  **[Install Dev Bridge Tools]** button opens a confirmation dialog showing
  the notice with a real link to the terms and an unchecked
  "I have read and agree to the Android SDK License Agreement" checkbox;
  the confirm action is disabled until it is checked. Only then does the UI
  POST the install.
- **API enforcement.** `POST /v1/devbridge/tools/install` requires an
  explicit `{"accept_license": true}` body field; anything else returns a
  `license_not_accepted` error code and performs no network activity. This
  boolean is the only request parameter, and it still influences no URL,
  hash, or path (preserving the plan's no-parameter rule for those). The
  daemon-side check means no client — including a future one — can trigger
  the download without asserting acceptance.
- **Upgrades.** An update to a newer pinned version re-presents the notice;
  acceptance is per-action, not a stored waiver that silently authorizes
  future downloads.

## Metadata recorded

**Pin file** (`backend/vr_hotspotd/devtools/platform_tools_pin.json`), in
addition to the plan's fields (`schema_version`, `version`, `url`,
`archive_sha256`, `max_archive_bytes`, `arch`, `extract_allowlist`):

- `license_name`: `"Android SDK License Agreement"`;
- `license_terms_url`: `"https://developer.android.com/studio/terms"`;
- `license_review`: `{ "reviewed_on": "<date>", "reviewed_by":
  "<maintainer>", "summary": "<one-line conclusion>", "record":
  "<repo-relative path to the review record>" }` — the dated maintainer
  review of the then-current terms text that this decision requires (see
  below). A pin **version bump re-checks and re-dates this field**; CI
  rejects a pin whose `license_review` is missing or empty.
- `extract_allowlist` must include the archive's notice file(s)
  (`platform-tools/NOTICE.txt`); a pin without a notice file in the
  allowlist is invalid.

**Installed-tools ledger** (`/var/lib/vr-hotspot/devtools/manifest.json`), in
addition to the plan's fields (`schema_version`, `version`, `source_url`,
`archive_sha256`, per-file `files`, `installed_at`):

- `license`: `{ "name": "Android SDK License Agreement", "terms_url":
  "https://developer.android.com/studio/terms", "accepted": true,
  "accepted_at": "<ISO-8601>", "accepted_via":
  "installer" | "cli" | "web" }`. No usernames or other PII — the surface
  identifier only. `tools status` and the support bundle's
  `vr-hotspot/devbridge.json` may report these fields (they contain no
  secrets and pass the existing redaction path unchanged).

`backend/vendor/VENDOR_MANIFEST.json` scope is **unchanged**: no
Platform-Tools bytes are ever repo-vendored, so no manifest entry exists;
the pin file plus the installed ledger are the provenance record, validated
by a new light CI check.

## What remains blocked

This document resolves *which path* and *under what conditions*. It does not
substitute for reading the license. Before the implementation PRs may land:

1. **Dated terms review (the residual blocker).** A maintainer must read the
   current Android SDK License Agreement text at
   `https://developer.android.com/studio/terms` and record a dated review
   (referenced from the pin file's `license_review`) confirming that the
   user-accepted, user-triggered, direct-from-Google, no-redistribution
   model specified here is consistent with the then-current terms. If the
   review finds otherwise, Option B is off and the feature falls back to
   Option A (distro packages + discovery only).
2. **Real pin values.** The pin file needs the reviewed version, exact URL,
   and independently computed SHA-256 of that exact archive.
3. **Documentation updates in the same implementation PR**: `docs/security.md`
   gains the egress-honesty section (first outbound request: single pinned
   `dl.google.com` host, user-triggered only, license-gated);
   `docs/dev-bridge.md` and CLI help drop the unqualified "read-only" claim
   in favor of "read-only except explicit, user-confirmed Dev Bridge tools
   management"; the plan's Licensing bullet is updated to point here.
4. Everything else the plan already gates on (SELinux verification on
   Fedora/Bazzite before advertising there, `unsupported_arch` handling,
   the open questions in the plan) remains as stated there.

Until item 1 is recorded, automated download, caching, and installation stay
blocked, exactly as the plan says today.

## Security and provenance requirements (binding on the implementation)

Carried over from the plan and made conditions of this decision:

- HTTPS only; URL host exactly `dl.google.com`; no user-supplied URLs,
  hashes, versions, or paths anywhere in the API/CLI/installer.
- Archive SHA-256 verified against the pin **before extraction**; hard fail
  and staging cleanup on mismatch; byte cap and bounded timeout enforced.
- Allowlist-only extraction with zip-slip defenses (reject absolute paths,
  `..`, symlink/hardlink entries, unexpected types, size anomalies); 0700
  random-suffix staging; atomic final move; archive deleted after install.
- Per-file SHA-256 + modes recorded in the ledger; `tools status`
  re-verification stays diagnostic, not enforcing.
- The daemon never executes the downloaded binary; execution is always the
  user's own action; the `tests/conftest.py` `adb` hard-block stays
  satisfied.
- Uninstall/reinstall cleanup as in the plan (`rm -rf /var/lib/vr-hotspot`
  already removes `devtools/`); nothing under any user home is touched.
- New pin-file CI check (`tools/ci/`): schema valid; https + exact
  `dl.google.com` host; well-formed lowercase SHA-256; non-empty allowlist
  that includes the notice file(s); `license_name`, `license_terms_url`,
  and a non-empty `license_review` present. Deterministic and offline after
  checkout (vendor-policy rule 8) — CI never fetches the archive or the
  terms page.

## Tests required for the implementation

In addition to the full test matrix already specified in
`docs/devbridge-adb-tools-plan.md`, the license-acceptance conditions add:

- **API gating:** `POST /v1/devbridge/tools/install` without
  `accept_license: true` (absent, false, wrong type) returns
  `license_not_accepted` and provably performs no download attempt (mocked
  transport asserts zero requests); with `true` it proceeds. The field
  never reaches URL/hash/path logic.
- **CLI gating:** on a TTY, the notice (terms URL, pinned URL, SHA-256,
  destination) is printed and a declined acceptance aborts with no POST;
  `--yes` alone does not send `accept_license`; non-TTY without
  `--accept-android-sdk-license` exits with the pointer message and no
  POST; the flag plus non-TTY proceeds.
- **Installer gating:** interactive default is No; the notice block
  (including the terms URL) is printed before any opt-in install path runs;
  non-interactive default skips; `--install-devbridge-tools` /
  `VR_HOTSPOT_INSTALL_DEVBRIDGE_TOOLS=1` proceed and the daemon POST they
  trigger carries `accept_license: true`; failure remains non-fatal.
- **Ledger fields:** a successful install writes the `license` block with
  `accepted: true`, a plausible `accepted_at`, and the correct
  `accepted_via`; `tools status` and the support-bundle collector surface
  it; redaction tests unchanged.
- **Web contract:** `assets/index.html` contains the dialog, terms link,
  and checkbox element ids; the `vm`-harness tests assert the confirm
  action is disabled until the checkbox is checked, the POST body carries
  `accept_license: true`, and the `license_not_accepted` error code renders
  its friendly text.
- **Pin CI:** missing/empty `license_review`, missing `license_terms_url`,
  wrong host, http URL, malformed sha256, or an allowlist without a notice
  file each fail the check.
- **Upgrade path:** an update install re-requires acceptance (API called
  with `accept_license: true` again; UI re-shows the dialog).

## Product-direction check

The preferred outcome — a SteamOS beginner clicking
**[Install Dev Bridge Tools]** and ending up with working `adb`, no
terminal, no `steamos-readonly disable` — survives intact under Option B.
The only UX addition is one honest dialog: Google's tools, Google's terms,
your explicit yes. That is the cost of not becoming a redistributor and not
bypassing the license gate, and it is a one-click cost.
