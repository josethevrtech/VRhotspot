# Dev Bridge Platform-Tools license review record

Status: license review record and pin metadata — no downloader, install
endpoint, or UI behavior in this PR

Date: 2026-07-25

Reviewer: josethevrtech (VRhotspot maintainer)

This is the review record referenced by
`backend/vr_hotspotd/devtools/platform_tools_pin.json` (`license_review.record`).
It records the dated maintainer review that
`docs/devbridge-platform-tools-license-decision.md` requires before any
Platform-Tools download path may be implemented, and it records the verified
pin values that unblock the future downloader implementation PR.

## What was reviewed

- The Android SDK License Agreement as published at
  <https://developer.android.com/studio/terms>, which governs the prebuilt
  `platform-tools_*.zip` archives served from `dl.google.com`.
- The licensing analysis and path selection in
  `docs/devbridge-platform-tools-license-decision.md` (Option B: user-driven
  download with explicit license notice and acceptance), governed by the
  policy rules in `docs/VENDOR_PROVENANCE_SBOM_PLAN.md`.

## Decision

**User-driven download with explicit acceptance; no redistribution**
(`user_driven_download_with_explicit_acceptance_no_redistribution` in the pin
file).

The user-accepted, user-triggered, direct-from-Google, no-redistribution
model specified in the decision document is the reviewed and recorded path:

- VRhotspot must **not** redistribute Platform-Tools in any form — no
  repository bytes, releases, CI artifacts, mirrors, proxies, API-served
  bytes, Flatpak payloads, or installer payloads.
- VRhotspot must **not** silently accept Google's terms. Explicit user
  acceptance of the Android SDK License Agreement remains required at install
  time on every surface (installer, CLI, Web Portal), before every download,
  including upgrades, exactly as the decision document's UX section defines.
- The bytes travel once, from Google to the accepting user's own machine, on
  that user's explicit instruction, from the single pinned versioned
  `dl.google.com` HTTPS URL — never a `latest` endpoint.
- The downloaded archive must be **deleted after extraction** in the future
  implementation; no archive cache is retained. The only persisted artifacts
  are the allowlisted extracted files (which must include
  `platform-tools/NOTICE.txt`) and the installed-tools ledger.
- A pin version bump re-requires a dated terms re-review and re-dates the
  pin's `license_review` block.

This record and the pin file record metadata and policy only. Nothing is
downloaded, vendored, or installed by this change, and
`backend/vendor/VENDOR_MANIFEST.json` scope is unchanged — no Platform-Tools
bytes are ever repo-vendored.

## Verified pin (2026-07-25)

| Field | Value |
| --- | --- |
| Version | `r37.0.0` |
| URL | `https://dl.google.com/android/repository/platform-tools_r37.0.0-linux.zip` |
| SHA-256 | `198ae156ab285fa555987219af237b31102fefe8b9d2bc274708a8d4f2865a07` |
| Size | 9,167,924 bytes |
| Verified at | 2026-07-25 |
| Verified by | josethevrtech (VRhotspot maintainer) |

How the hash was verified, per this record's cross-check requirement:

- The archive was downloaded from the exact versioned URL above (never a
  `latest` endpoint) to a temporary directory outside the repository, hashed
  with SHA-256, downloaded a second time, and the two fetches compared
  byte-for-byte (identical).
- The digest was cross-checked against a second Google-published artifact:
  the official SDK repository manifest
  (`https://dl.google.com/android/repository/repository2-3.xml`) — the same
  metadata `sdkmanager` itself trusts — whose linux `r37.0.0` entry publishes
  SHA-1 `bcf323933980a59dccc3f14c339aed5fb2171163` and size `9167924`; both
  matched the downloaded archive exactly.
- Archive integrity was verified (`unzip -t`) and the listing confirmed the
  allowlisted paths `platform-tools/adb` and `platform-tools/NOTICE.txt`
  exist in the archive.
- The downloaded archives were **deleted after hashing**. No archive, binary,
  or extracted bytes entered the repository; only this metadata was recorded.

### Version re-confirmation

The placeholder pin named `r36.0.0`. On 2026-07-25 Google's SDK repository
manifest no longer lists `r36.0.0` at all (superseded), and `r37.0.1` is a
**dev-channel** release governed by the `android-sdk-preview-license`, not
the reviewed Android SDK License Agreement. `r37.0.0` is the current
**stable-channel** release governed by `android-sdk-license` (the Android SDK
License Agreement this record reviews), so the pin was re-confirmed as
`r37.0.0`. This version change is part of the same dated review recorded
here, satisfying the "version bump re-requires a dated terms re-review"
restriction.

## What this unblocks — and what it does not

With the real SHA-256 recorded and this dated maintainer review in place,
the pin sets `implementation_blocked: false`: the future downloader
implementation PR now has an auditable source of truth to build against.

Nothing else changes in this diff. All of the following remain
**unimplemented** and gated on their own reviewed PRs, exactly as the
decision document specifies: downloader code, extraction code,
`/v1/devbridge/tools` install/remove endpoints, installer and CLI opt-ins,
and any Web Portal / Flatpak UI for tools install. The implementation PRs
remain bound by the decision document in full, including:

- **No redistribution**, ever, in any form.
- **Explicit user acceptance** of the Android SDK License Agreement is still
  required at install time, before every download, on every surface;
  acceptance is never defaulted or implied.
- The implementation must verify the archive against this pin's SHA-256
  **before extraction**, **delete the archive after extraction**, and retain
  **no archive cache**.
- `docs/security.md` egress-honesty section, `docs/dev-bridge.md` read-only
  wording update, SELinux verification, and `unsupported_arch` handling
  remain binding on the implementation PRs and are not resolved here.

## CI check

`tools/ci/platform_tools_pin_check.py` (run in CI alongside the vendor
manifest check, covered by `tests/test_platform_tools_pin.py`) validates the
pin offline and deterministically: schema and required fields; HTTPS with the
host exactly `dl.google.com`; a specific versioned archive URL (never
`latest`); a well-formed lowercase SHA-256 or the explicit blocked
placeholder coupled to `implementation_blocked: true`; a non-empty sorted
`extract_allowlist` containing `platform-tools/adb` and
`platform-tools/NOTICE.txt`; the exact `license_name` and
`license_terms_url`; and a complete, non-empty `license_review` block whose
`record` path exists in the repository. CI never fetches the archive or the
terms page.
