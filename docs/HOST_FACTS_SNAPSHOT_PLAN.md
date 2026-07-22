# HostFactsSnapshot transition and architecture

Status: operational migration and final transition cleanup complete

Date: 2026-07-22

This document began as the design plan in PR #62. It now records the landed
architecture and the boundaries that must remain stable after the transition.
The final cleanup is documentation, test clarity, and dead-code audit only. It
does not authorize a runtime, policy, API, UI, installer, or host-mutation
change.

## Completion status

| Stage | Pull request | Status | Result |
|---|---:|---|---|
| Design and migration plan | #62 | Complete | Defined the operation snapshot, read-only boundary, parity gates, and staged cutover. |
| Model and builder | #63 | Complete | Added the immutable model, read-only builder, bounded evidence, and fake-runner tests. |
| Preflight migration | #64 | Complete | Canonical preflight consumes snapshot-owned platform, service, firewall, adapter, regulatory, concurrency, and uplink facts while preserving its response contract. |
| Adapter inventory/readiness migration | #65 | Complete | Inventory projects snapshot adapter facts into the existing shape; readiness consumes the same factual generation without changing scoring or recommendation policy. |
| Lifecycle selection migration | #66 | Complete | Start builds one pre-mutation operation snapshot for adapter selection and the fail-closed active-uplink guard, including retry/reselection paths. |
| Duplicate-probe cleanup check cycle | #67 | Superseded | Original cleanup PR/check cycle; recreated as #68 after branch deletion/recovery. |
| Duplicate-probe cleanup | #68 | Complete | Merged cleanup; consumers bypass duplicate direct probes when complete equivalent snapshot facts are available; intentional live and compatibility paths remain. |
| Final transition cleanup | PR 6 | Complete | Updated status and architecture documentation, corrected stale comments, retained regression coverage, and audited dead code without changing behavior. |

“Duplicate-probe cleanup complete” does not mean every direct probe was
deleted. No-snapshot compatibility paths, incomplete-snapshot fallbacks,
standalone status/repair/debug paths, strict channel discovery, and live
post-start route/NAT discovery remain where their behavior is not equivalent to
reusing a pre-mutation fact.

## Current architecture

`HostFactsSnapshot` is the shared read-only host-facts foundation. A fresh,
immutable snapshot represents one bounded collection window and carries both
normalized facts and the probe records/errors that produced them. It is not a
global cache and is not itself a public API response.

The model contains:

- snapshot identity, operation kind, timestamps, duration, and builder version;
- platform and OS-release facts;
- default routes and the compatibility-selected uplink interface;
- one `iw dev` view, one factual view per discovered phy, and regulatory facts;
- NetworkManager, iwd, and firewall facts;
- adapter facts derived from those captures; and
- bounded probe records and errors for missing, timed-out, denied, malformed,
  failed, or truncated inputs.

Policy remains outside the model and builder. Adapter scoring, readiness,
Basic-mode visibility, lifecycle selection, active-uplink rejection, preflight
issues, engine choice, firewall mutation, and rollback keep their existing
owners.

### Consumers

| Consumer | Snapshot use | Deliberately separate work |
|---|---|---|
| Canonical preflight | Creates one `diagnostics_preflight` snapshot unless one is injected. Snapshot facts feed platform, service, firewall, inventory, readiness, concurrency, and active-uplink report fields. | Runtime-binary inspection, rfkill, subnet checks, and other legacy preflight inputs remain separately collected. Existing conservative fallbacks remain when a required snapshot projection is incomplete. |
| Adapter inventory API | Creates one `adapter_inventory` snapshot and projects `AdapterFacts` into the existing inventory dictionary and response envelope. | The no-snapshot inventory path remains for compatibility consumers and fallback paths. |
| Adapter readiness API | Creates one `adapter_readiness` snapshot. Inventory and readiness consume that same object, while readiness remains a pure policy projection. | Scoring, reasons, ordering, recommendation, and Basic-mode policy are unchanged. |
| Lifecycle start | Creates one `lifecycle_start` snapshot before adapter selection and before NetworkManager, iwd, interface, regulatory, engine, firewall, or tuning mutation. | Live mutation verification, channel-candidate discovery, AP readiness, telemetry, and post-start network operations remain outside the snapshot where current behavior requires current state. |

Lifecycle uses the operation snapshot for its selected adapter, platform and
firewall projections when complete, and the default-uplink role decision. The
same snapshot is passed into repair preparation and every supported
retry/reselection path. A retry can reconsider only adapters already present in
the operation inventory; a newly named interface is not silently adopted as a
new factual generation.

### Read-only collection budget

For one build, tool resolution is memoized within the builder and the factual
sources are bounded as follows:

| Source | Maximum collection per snapshot | Command timeout |
|---|---:|---:|
| OS release | One injected file read | Not applicable |
| `iw dev` | One command | 3.0 seconds |
| `iw phy <phy> info` | One command per phy discovered by `iw dev` | 4.0 seconds each |
| `iw reg get` | One command | 2.0 seconds |
| `ip route show default` | One command | 2.0 seconds |
| `nmcli ... RUNNING` | One command | 1.0 second |
| `systemctl is-active` | One command each for NetworkManager, iwd, firewalld, and ufw | 1.0 second each |
| `firewall-cmd --state` | One command | 1.0 second |
| `ufw status` | One command | 1.5 seconds |
| `iptables --version` | One command | 1.0 second |
| Adapter device link | One injected sysfs read per observed interface | Not applicable |

Command capture is limited to 64 KiB per command. Error messages are sanitized
and limited to 240 characters; OS-release entry count and values are bounded.
The builder returns partial facts plus explicit errors instead of escalating
privileges, retrying with mutation, or fabricating a negative fact. SSID values
are not retained.

## Operational boundaries

### Pre-start safety facts

The lifecycle snapshot answers the safety question that must precede host
mutation: which factual adapter generation was selected, and was that adapter
the captured active default-route interface? An equal adapter/uplink role is
rejected, and an indeterminate default-uplink fact fails closed. Selection and
the guard therefore use one immutable generation rather than independently
timed probes.

Retries and reselection reuse the same snapshot guard because they are still
part of the same start operation. Re-probing only after a failed attempt could
mix an earlier selection with a later route or adapter generation and allow a
replacement interface to bypass the original safety boundary.

### Post-start operational route/NAT facts

Post-start route and NAT discovery remains live and separate by design. Engine
processes and `network_tuning` discover the current default route when applying
their operational network work after interface and engine changes.

A pre-start route can become stale while repair, interface ownership,
regulatory setup, engine startup, or the host network changes. Reusing that
stale interface for post-start NAT could configure forwarding against a route
that no longer exists or is no longer preferred. The pre-start snapshot must
therefore protect adapter/uplink role safety without replacing the live route
used for post-start NAT discovery.

Live AP readiness, mutation-result verification, station telemetry, channel
scans, transmit-power reads, and strict channel/regulatory candidate probes are
also not promoted into a long-lived snapshot. None may replace or weaken the
original adapter/uplink role decision.

## Compatibility and failure boundaries

- Snapshot facts are tri-state where success, negative evidence, and unknown
  must remain distinguishable.
- A failed, truncated, malformed, missing, or permission-denied probe remains
  visible through bounded provenance.
- Consumers preserve their existing public shapes and error policy. Where a
  complete equivalent snapshot projection is unavailable, an existing legacy
  fallback can still run instead of inventing parity.
- Direct probes used by those compatibility/fallback paths remain read-only.
  Mutating commands do not move into `host_probes.py` or the snapshot builder.
- Snapshot types remain internal. Public reports expose curated projections,
  not raw command output, upstream SSIDs, secrets, or exception objects.

## Non-goals and deferred work

- No same-radio STA+AP promise. Concurrency evidence remains informational and
  never bypasses the active-uplink guard.
- No Steam Frame support or detection work in this PR.
- No configuration validation work in this PR.
- No Bazzite policy cleanup in this PR.
- No vendor SBOM, provenance, or checksum-manifest work in this PR.
- No adapter scoring/recommendation, preflight, lifecycle, retry/fallback,
  Pop!_OS, watchdog, API, UI, installer, firewall, engine, passphrase,
  support-bundle, authentication, CLI, network-tuning, or post-start discovery
  behavior change in this PR.

## Dead-code audit result

The final audit found stale first-PR wording in the snapshot model and builder;
that wording is updated to describe their current consumers. It did not find a
production helper that could be removed safely within this closeout scope.

The direct-probe groups remain reachable for specific reasons:

- no-argument adapter inventory is used by compatibility API/status code,
  preflight fallback, standalone lifecycle repair, band-6 helpers, and Wi-Fi
  probe callers that do not receive an inventory;
- default-uplink probing is used by the three engine processes and
  `network_tuning` for live post-start route/NAT discovery;
- Wi-Fi host-context wrappers preserve the default debug/report shape, while
  the lifecycle's Wi-Fi-only path bypasses that duplicate context;
- platform direct probes serve the standalone platform/status surface and
  incomplete-snapshot fallbacks; and
- preflight's no-snapshot and incomplete-projection paths preserve current
  conservative failure behavior.

The old/new parity tests remain useful regression tests for response shape,
unknown-fact handling, scoring, selection, and safety ordering. They are not
removed merely because the transition has completed.

## Validation expectations

Closeout validation includes shell syntax checks for both top-level and backend
install/uninstall scripts, the installer matrix check, the full pytest suite,
`git diff --check`, and explicit status/stat/name-status review. Unit tests keep
the real-system-command guard enabled and use fake runners or mocks.

The regression suite specifically preserves:

- immutable/bounded builder behavior and partial-failure evidence;
- preflight and adapter old/new parity plus public response shapes;
- one snapshot per adapter/preflight/lifecycle operation;
- fail-closed initial and retry/reselection active-uplink guards before
  mutation;
- no adoption of an unobserved re-enumerated interface;
- duplicate-probe bypass when complete snapshot facts exist;
- legacy fallback behavior when they do not; and
- live post-start uplink discovery for NAT/network tuning.

## Future work

The following items require separately approved, separately scoped changes:

- configuration cross-field validation;
- Bazzite policy-mismatch cleanup;
- vendor provenance, SBOM generation, and a checksum manifest; and
- Steam Frame dongle-detection research only after explicit approval and real
  VID/PID evidence.

Any future policy, payload, mutation-order, or platform-support change must be
reviewed on its own merits and must not be presented as HostFactsSnapshot
transition cleanup.
