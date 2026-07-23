# Vendor provenance, SBOM, and checksum manifest plan

Status: PR #72 documentation-only plan

Date: 2026-07-22

This document defines the supply-chain groundwork for files that VRhotspot
ships from the repository. It does not add a manifest, generate an SBOM,
verify a checksum, or change installation or runtime behavior.

## Problem

VRhotspot ships or relies on bundled/vendor assets so it can provide a
consistent networking stack on Linux distributions whose packages and feature
sets differ. Some of those files can execute in the privileged daemon's trust
boundary: the backend installer copies the repository payload under
`/var/lib/vr-hotspot/app`, the service includes the bundled `bin` and `lib`
directories in its execution environment, and platform policy can select the
bundled networking stack.

The existing repository has useful but fragmented attribution:

- `backend/vendor/README.md` describes bundle selection and records some
  versions, but the dnsmasq version is still a placeholder.
- `THIRD_PARTY_NOTICES.md` names the current upstream projects and licenses.
- `backend/vendor/licenses/` contains license texts or references.
- `README.md` lists the bundled components.
- `docs/support-bundle.md` anticipates reporting bundled component versions,
  but the implemented support bundle does not report file provenance or
  checksum state.

There is no canonical machine-readable vendor inventory, generated SBOM, or
reviewed checksum manifest today. A successful version probe also does not
prove that a file came from the documented source or that its bytes match a
reviewed artifact.

Future hardware support may increase pressure to add firmware, helper tools,
driver-related material, device metadata, or other vendor-specific files. The
repository needs explicit provenance, redistribution, review, and update rules
before that pressure produces more opaque assets.

## Current inventory observation

This is an observation for planning, not the future manifest and not an
approval of the current provenance records. PR #73 must inventory files
individually and resolve or explicitly mark unknown facts.

| Repository area | Files observed | Current documentation | Trust relevance |
|---|---|---|---|
| `backend/vendor/bin/` | `dnsmasq`, `hostapd`, `hostapd_cli`, `lnxrouter` | dnsmasq version unrecorded; hostapd v2.11; linux-router v0.8.1; licenses and upstream project URLs named | Three x86-64 ELF executables and one executable shell program may be selected for networking operations. |
| `backend/vendor/lib/` | `libnl-3.so.200`, `libnl-cli-3.so.200`, `libnl-genl-3.so.200`, `libnl-route-3.so.200` | libnl 3.10 and LGPL-2.1 are named | Executable-mode x86-64 shared libraries may be loaded with bundled networking programs through `LD_LIBRARY_PATH`. |
| `backend/vendor/licenses/` | `dnsmasq.LICENSE.txt`, `hostapd.LICENSE.txt`, `libnl.LICENSE.txt`, `linux-router.LICENSE.txt` | License identifiers, upstream links, and a partial libnl notice | Attribution/control material; its presence does not by itself establish the exact payload source. |
| `backend/vendor/README.md` | bundle README | Partial version and update notes | Human-readable control material that is not machine-enforced. |

All 13 files above are tracked. No profile-specific directories are present in
the current tree even though runtime lookup supports profile-specific `bin`
and `lib` directories.

The PR #73 scope audit must also classify repository-copied third-party assets
outside `backend/vendor/`. In particular, `assets/qrcode.js` contains an
upstream origin and MIT license notice, while `assets/chart.js` appears to be a
prebuilt Chart.js distribution but has no visible version or license banner in
the checked-in file. These are audit candidates, not newly approved provenance
claims. Project-authored images, scripts, and generated assets must be
distinguished from copied third-party material using evidence rather than file
extension or location alone.

System-provided hostapd, dnsmasq, and libraries are outside the file-level
vendor manifest because their bytes are owned by the host package manager.
The future SBOM may describe them as external runtime dependencies, but it must
not imply that VRhotspot supplied or checksummed those host files.

## Goals

- Inventory every vendored file, including executable payloads, shared
  libraries, license/control material, profile-specific variants, and copied
  third-party assets outside the current vendor directory.
- Document the source and provenance of every inventoried file.
- Document its license and redistribution status, including an explicit
  unknown or blocked state where evidence is incomplete.
- Document its purpose and runtime trust boundary.
- Add one canonical machine-readable vendor manifest in a later PR.
- Derive a deterministic SBOM from reviewed manifest data rather than maintain
  a second hand-edited source of truth.
- Add CI coverage for manifest completeness and schema validity in a later PR.
- Add reviewed checksum verification in a later PR, likely in CI before any
  installer or runtime use.
- Expose bounded, sanitized provenance status in support bundles later.
- Make the acquisition and review process repeatable before another vendor
  asset can be added or updated.

## Non-goals for PR #72

- No runtime enforcement.
- No installer behavior change.
- No CI behavior change.
- No checksum verification yet.
- No machine-readable manifest or generated SBOM yet.
- No new or replaced vendored binaries, libraries, firmware, scripts, or
  other vendor files.
- No Steam Frame driver support.
- No automatic Steam depot downloads.
- No proprietary driver redistribution.
- No Flatpak packaging or Flatpak architecture implementation.
- No known-adapter registry.
- No Steam Frame or VR Direct Link detection, adapter scoring, or other
  hardware behavior.
- No HostFactsSnapshot change or new consumer.

## Policy boundaries

The following rules govern subsequent implementation work:

1. A newly added or previously untracked binary/vendor asset must not be
   accepted without a manifest entry in the same review. Before PR #73 lands,
   vendor additions remain blocked rather than bypassing the missing schema.
2. Every manifest path must resolve to one repository-controlled file inside
   an explicitly covered scope. The validator must reject duplicate paths,
   traversal, ambiguous normalization, undeclared symlinks, and entries for
   missing files.
3. No proprietary driver binary, firmware, or vendor utility may be
   redistributed unless its license explicitly permits redistribution in the
   proposed form. An unknown or unclear license is a blocked status, not an
   inference of permission.
4. The installer must not automatically download Steam depots or use Steam as
   an implicit binary source. User-supplied evidence may be parsed in a future
   explicitly approved research workflow, but the repository must retain only
   derived safe metadata unless licensing clearly permits retaining more.
5. Raw user-supplied driver packages, depot contents, firmware, device dumps,
   credentials, tokens, account data, and other unrelated content must not be
   committed or included in support bundles.
6. Existing bundled hostapd-, dnsmasq-, lnxrouter-, and libnl-style assets must
   be fully documented before checksum enforcement is introduced. Current
   filenames, executable bits, version output, or notices are evidence inputs;
   none alone proves origin or integrity.
7. A changed payload byte, executable bit, source reference, license status,
   platform allowlist, or trust-boundary classification requires explicit
   manifest review. Checksum-only update commits without a source/update
   explanation are insufficient.
8. CI verification must be deterministic and offline after checkout. It must
   not fetch an upstream file and silently treat the latest response as the
   expected artifact.
9. The manifest and generated SBOM are evidence and inventory mechanisms, not
   malware analysis, legal approval, or proof that an upstream build is safe.
10. Installer or runtime enforcement requires a separate design and explicit
    approval after the documentation, manifest, CI coverage, and checksum
    process have demonstrated stable behavior.

## Future manifest contract

PR #73 should choose a documented format and schema for one canonical manifest.
Its control file should live outside the payload namespace it hashes where
practical; otherwise the schema must explicitly exclude the manifest from
self-hashing while CI still validates that control file. Unknown historical
facts must be represented honestly and assigned a review status rather than
filled with guesses.

Each file entry needs at least these fields:

| Field | Required meaning |
|---|---|
| `path` | Normalized repository-relative path; unique and case-sensitive. |
| `file_type` | Controlled value such as ELF executable, shared library, script, browser JavaScript, firmware, license text, or documentation. |
| `executable` | Boolean matching the reviewed Git executable bit, independently of `file_type`. |
| `purpose` | Why VRhotspot ships the file and which component consumes it. |
| `source_project_or_vendor` | Upstream project, vendor, or documented project-authored origin. |
| `upstream_url_or_source_note` | Stable upstream URL or an explicit source note when no public URL applies. |
| `version`, `commit`, `release` | Exact version evidence when known; fields may be null only with an explicit unknown status and reviewer note. |
| `license` | SPDX identifier when reliable, otherwise the exact declared license name. |
| `license_status` | Reviewed redistribution state such as allowed, restricted, blocked, or unknown, with evidence linkage. |
| `sha256` | Lowercase SHA-256 of the exact checked-in bytes; no newline or binary normalization. |
| `allowed_platforms` | Explicit platform/profile allowlist or a reviewed `all-supported` value. |
| `runtime_trust_boundary` | Whether the file is privileged executable code, dynamically loaded code, unprivileged/browser code, data, or documentation-only material. |
| `update_process` | Repeatable acquisition/build, verification, license-review, replacement, and test procedure. |
| `reviewer_notes` | Gaps, build flags, source/build reproducibility, exceptions, or evidence needed for the next review. |

The schema may add component IDs, relationships, build recipe references,
source archive hashes, package URLs, CPEs, or SBOM identifiers. Those additions
must not weaken per-file coverage or collapse an unknown fact into a component-
level assumption.

### Manifest and SBOM roles

The vendor manifest is the repository's reviewed file-level source of truth.
An SBOM should be generated deterministically from it in SPDX or CycloneDX
format once PR #73 has selected the representation and PR #74 has a validation
path. The SBOM should identify shipped third-party components, versions,
licenses, file relationships, and external system dependencies where useful.

The generated SBOM must not become a separately edited inventory. CI or a
release process should be able to reproduce it from the same reviewed manifest,
and generation must not require downloading current upstream metadata. A
component appearing in the SBOM does not replace its per-file checksum or
redistribution review.

## Staged roadmap

| Stage | Scope | Exit condition |
|---|---|---|
| PR #72 | This documentation-only provenance, SBOM, and checksum-manifest plan. | Boundaries, current gaps, schema fields, stages, and future acceptance criteria are reviewable with no behavior change. |
| PR #73 | Add the canonical vendor manifest and complete the initial provenance/license inventory. Classify copied third-party assets outside `backend/vendor/` and define deterministic SBOM output. | Every covered current file has one honest entry; unknowns are explicit; no payload is added or replaced merely to complete the inventory. |
| PR #74 | Add CI schema and manifest-coverage checks, plus deterministic SBOM generation or validation once the chosen format is stable. | CI fails for missing, extra, duplicate, invalid, or stale manifest paths and can reproduce the reviewed SBOM representation. |
| PR #75 | Add checksum verification, likely CI-only first. | Exact checked-in bytes and executable modes match reviewed entries; changes require a manifest diff; installer/runtime behavior remains unchanged unless separately approved. |
| PR #76 | Add sanitized support-bundle provenance output. | A bundle can report bounded component, selection, provenance, and checksum status without arbitrary file reads or secret disclosure. |
| PR #77 | Write the Flatpak architecture plan. | The UI/control-app boundary, daemon API, host installation/update ownership, and trust/update story are explicit before packaging starts. |
| PR #81+ | Begin Steam Frame / VR Direct Link evidence and adapter-intelligence work only after explicit approval. | Research starts from lawful, user-provided or public evidence and derived metadata, not redistributed drivers or automatic depot downloads. |

## Acceptance criteria for future PRs

### Manifest and provenance

- The manifest has complete coverage of every file in its declared vendor
  scopes, including profile variants and non-executable license/control files.
- The scope audit has classified copied third-party files outside
  `backend/vendor/`; each confirmed vendor asset is covered or moved under a
  covered namespace in a separately reviewed change.
- Every entry has a purpose, source record, license/status, platform allowlist,
  trust-boundary classification, update process, and reviewer notes.
- Exact version, commit, or release data is recorded where evidence supports
  it; unresolved data is visibly unknown and does not claim redistribution
  approval.
- The SBOM is reproducible from canonical manifest data and is not a divergent
  hand-maintained list.

### CI and checksums

- CI fails when a covered vendor file is absent from the manifest, a manifest
  path is absent from the tree, an entry is duplicated, or the schema is
  invalid.
- CI checks repository file mode and type so a payload cannot become
  executable without review.
- Checksums are stable, exact, reproducible, and reviewed against provenance
  evidence. A payload change and its checksum change are visible together.
- Checksum verification is exercised in CI before any installer or runtime
  enforcement is proposed.
- Generated SBOM and validation outputs are deterministic and require no
  unreviewed network input.

### Support output and enforcement gate

- The support bundle can report manifest/schema version, relative component
  path or ID, declared source/version/license status, selected/not-selected
  state where known, and checksum state such as `match`, `mismatch`, `missing`,
  or `unreadable`.
- Support collection reads only allowlisted manifest paths, does not follow
  arbitrary symlinks, does not include payload bytes, and applies the existing
  redaction policy before archive creation.
- The vendor provenance report is clearly distinct from the support archive's
  existing `manifest.json`, which inventories bundle contents rather than
  shipped software provenance.
- Installer or runtime enforcement is considered only after the documentation,
  complete manifest, CI coverage, and reviewed checksums have proved stable.
  Any enforcement proposal must separately define failure behavior, recovery,
  offline operation, upgrades, rollback, and platform compatibility.

## Longer roadmap boundary

A future Flatpak UI/control application depends on a stable daemon API and this
trust groundwork. Flatpak packaging must not become a way to hide unresolved
ownership of privileged host components or vendor updates.

Steam Frame dongle and VR Direct Link adapter support should begin with
evidence collection, not drivers. MediaTek mt76/MT7921AU Wi-Fi 6E may later be
the first friendly-competition lab target for documented, reproducible tests.
MT7925 Wi-Fi 7 and other hardware candidates remain future/lab-only until real
hardware evidence, Linux driver behavior, regulatory constraints, and explicit
approval establish support.

Nothing in this roadmap authorizes driver redistribution, automatic Steam
depot access, a known-adapter registry, adapter behavior changes, or a support
claim for unproven hardware.
