# Dev Bridge Platform-Tools license review record

Status: license review record and pin metadata — no downloader, install
endpoint, or UI behavior in this PR

Date: 2026-07-25

Reviewer: josethevrtech (VRhotspot maintainer)

This is the review record referenced by
`backend/vr_hotspotd/devtools/platform_tools_pin.json` (`license_review.record`).
It records the dated maintainer review that
`docs/devbridge-platform-tools-license-decision.md` requires before any
Platform-Tools download path may be implemented, and it states exactly what
remains blocked after this record lands.

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

## What remains blocked

**Downloader implementation remains blocked.** The pin's `archive_sha256` is
an intentionally invalid, obvious placeholder
(`BLOCKED-PLACEHOLDER-DO-NOT-IMPLEMENT-DOWNLOADER-SHA256-NOT-INDEPENDENTLY-VERIFIED`)
because the SHA-256 of the pinned archive has not been independently
verified, and per vendor-policy rule 8 CI must never fetch the archive and
trust the response. Accordingly the pin sets `implementation_blocked: true`,
and `tools/ci/platform_tools_pin_check.py` rejects a pin whose hash is the
placeholder without that flag.

Until the real hash is recorded, all of the following stay unimplemented:
downloader code, `/v1/devbridge/tools` install/remove endpoints, installer
and CLI opt-ins, and any Web Portal / Flatpak UI for tools install.

To unblock, one reviewed diff must:

1. Record the real lowercase SHA-256 of the exact pinned archive,
   independently computed (download the pinned URL after personally accepting
   the Android SDK License Agreement, hash it, and cross-check the digest
   from a second independent host/network path — never from a single fetch
   treated as truth).
2. Re-confirm that the pinned `version`/`url` is the intended current
   release, and re-read the then-current terms text, re-dating the pin's
   `license_review` if the pin version changes.
3. Flip `implementation_blocked` to `false` in the same diff.

Everything else the decision document gates on (acceptance UX and API
enforcement, `docs/security.md` egress-honesty section, `docs/dev-bridge.md`
read-only wording update, SELinux verification, `unsupported_arch` handling)
remains binding on the implementation PRs and is not resolved here.

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
