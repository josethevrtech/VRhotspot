# Per-operation host-facts snapshot plan

Status: design and audit only

Date: 2026-07-21

Implementation status: not started

This document plans a future behavior-preserving refactor. It does not define a
new API contract, enable a new adapter role, or authorize host mutation changes.
The active-uplink fail-closed guard, missing-token fail-closed behavior,
watchdog 5 GHz channel persistence fix, exact UFW inactive parsing, diagnostic
input budgets, non-argv passphrase transport, and installer-owned firewall
rollback are the baseline. They are not reopened here.

The plan follows the repository audit's sections 8.1, 11-13, 19, 21-22, 28,
and 30: retain `host_probes.py` as the normalized read-only seam, add one
per-operation snapshot before broader refactoring, and keep privileged mutation
and rollback work separate.

## Problem statement

VRhotspot has shared parsers and several shared read-only probes, but it does
not have one immutable observation of the host for a request or lifecycle
operation. Inventory, readiness, preflight, selection, lifecycle, engine
helpers, and tuning can run overlapping commands at different times.

That creates three related problems:

- `iw dev`, `iw phy`/`iw list`, `iw reg get`, the default route, firewall
  status, NetworkManager state, and iwd state can change between reads. Two
  consumers in one operation can therefore reach different conclusions.
- A start can select an adapter and check its uplink role, mutate the host, and
  later re-read adapter, regulatory, service, or route facts. This makes it
  difficult to prove that the safety decision and the eventual action used the
  same evidence.
- Diagnostics can describe a different host than lifecycle evaluated, even
  when both are individually using valid read-only probes. Probe failures are
  also represented inconsistently: some become `False` or `None`, some become
  fallback dictionaries, and some raise.

The intended result is not an atomic kernel transaction. It is one bounded
collection window whose facts, errors, and provenance are reused throughout a
single operation.

## Terms and operation boundary

- **Fact**: an observation such as the first default-route interface, an
  adapter-to-phy mapping, or the output-derived AP capability of a phy.
- **Policy**: a decision based on facts, such as current adapter scoring,
  Basic-mode visibility, active-uplink rejection, or firewall backend choice.
- **Snapshot**: immutable normalized facts plus the results and errors that
  produced them. It contains no policy mutation methods.
- **Operation**: one diagnostics/preflight request, one inventory/readiness
  request, or one serialized lifecycle start/repair attempt. A snapshot is not
  cached globally or reused by a later operation.
- **Host mutation boundary**: the first action that can change an interface,
  association, service, regulatory domain, route/firewall state, driver, or
  engine process. Daemon state-file bookkeeping is not a source of host facts,
  but it also must not trigger snapshot refreshes.

Post-start AP readiness checks, station telemetry, channel scans, and transmit
power reads are runtime observations, not pre-operation facts. They may remain
live, but they must not silently replace the snapshot used for selection or a
safety guard.

## Current host-fact sources

### Fact-to-source map

| Fact | Current collectors and consumers | Duplication or disagreement risk |
|---|---|---|
| OS and platform | `diagnostics/platform.py`, `os_release.py`, `wifi_probe.detect_os_flavor()`, lifecycle platform checks, `vendor_paths.py`, and supervisor binary preference | Raw OS release, family, immutability, and installer/runtime classifications are separate projections. |
| Default uplink | `host_probes.probe_default_uplink()` called by lifecycle, `network_tuning.py`, and the NAT, bridge, and 6 GHz engine processes; canonical preflight calls it independently | Lifecycle's active-uplink guard can use an earlier route than the engine or tuning layer. Wrapper error policies also differ intentionally. |
| `iw dev` | Adapter inventory, lifecycle AP/SSID/phy/readiness and cleanup helpers, and client diagnostics | Pre-operation inventory reads and post-mutation verification are not distinguished by type. |
| `iw phy` / `iw list` | Inventory calls `iw phy <phy> info` separately for AP, Wi-Fi 6, width, and band facts; concurrency can cause another call; `wifi_probe` separately runs `iw list` | One phy can be read several times in one inventory/report, and `iw list` can expose a different moment or output shape. Two inventory helpers also have process-wide `lru_cache` state. |
| Regulatory domain | Inventory runs `iw reg get`; `wifi_probe` runs it again; preflight consumes the inventory projection; lifecycle and engine processes separately run mutating `iw reg set` | Capability/channel policy may use facts captured before or after a regulatory mutation. |
| NetworkManager | `host_probes` queries global `nmcli` running state; platform diagnostics query the service; lifecycle and the NAT engine query global and per-device state | Service activity, `nmcli` activity, and per-interface ownership are different facts but are sometimes collapsed into one Boolean. |
| iwd | `host_probes` checks tools and service state; lifecycle and the NAT engine have separate service/tool fallback checks and association handling | Presence, activity, and selected-adapter association can be observed at different points around disconnect/restart mutations. |
| Firewall backend | `host_probes`, platform diagnostics, lifecycle backend selection, and engine-local firewalld checks | Presence, service activity, functional status, selected backend, and mutation ownership are distinct and can be reported differently. |
| Adapter inventory | `get_adapters()` is called by adapter/readiness API requests, preflight report collection, support-bundle collection, start, repair, and Pop!_OS recovery | Separate calls repeat all wireless facts; a late recovery inventory can change the selected interface after the original guard. |

### Module and command audit

Classification used below:

- **R**: observational/read-only.
- **A**: active observation that can cause radio activity or temporary
  disruption even though it does not persist configuration.
- **M**: potentially mutating or artifact-creating.

| Module | Facts and calls | Class and privilege/tool notes | Current coverage |
|---|---|---|---|
| `backend/vr_hotspotd/host_probes.py` | Normalized `CommandResult`; OS, `iw`, regulatory, uplink, NM, iwd, and firewall parsers/probes. Commands include `ip route show default`, `nmcli ... RUNNING`, `systemctl is-active iwd`, `firewall-cmd --state`, `ufw status`, and `iptables --version`. | R. Uses host tools and may receive missing-command, timeout, permission, or nonzero results. It must remain mutation-free and must never invoke `sudo`. | `tests/test_host_probes.py`, `tests/test_preflight_report.py`, and `tests/test_system_command_guard.py`. |
| `backend/vr_hotspotd/adapters/inventory.py` | `iw dev`, `iw reg get`, repeated `iw phy <phy> info`, and `/sys/class/net/<ifname>/device` bus detection; then current scoring/recommendation. | R. `iw`/netlink access and sysfs visibility are required. `_phy_supports_wifi6` and `_phy_supports_80mhz` have process-wide caches; other per-phy facts are re-read. | `tests/test_adapter_inventory.py`, `tests/test_adapter_inventory_virtual.py`, and compatibility cases in `tests/test_host_probes.py`. |
| `backend/vr_hotspotd/adapters/readiness.py` | Builds readiness, scores, reasons, and Basic-mode visibility from supplied inventory. | Pure; no tools and no root requirement. | `tests/test_adapter_readiness.py` and `tests/test_api_adapter_readiness.py`. |
| `backend/vr_hotspotd/diagnostics/preflight_report.py` | Collects platform, firewall, NM, iwd, binary, inventory, readiness, uplink, concurrency, and legacy preflight results. | Intended R. It uses `inspect_runtime_binaries()` and injected legacy-preflight capabilities to avoid lifecycle setup side effects. Every top-level collector has a fallback, but not every nested probe exposes its original command error. | `tests/test_preflight_report.py` and `tests/test_api_preflight_report.py`. |
| `backend/vr_hotspotd/preflight.py` | `rfkill list`, adapter regulatory facts, hostapd version/capability output, `ip addr show`, `ip route show`, and bridge-uplink sysfs existence. | Mostly R. When capabilities are not injected, `_resolve_hostapd_path()` calls supervisor `_build_engine_env()`, which can probe binaries, append notes, and create a temporary symlink directory. That path is M and must not enter snapshot collection. | `tests/test_preflight_probes.py`, `tests/test_host_probes.py`, and report tests with injected capabilities. |
| `backend/vr_hotspotd/wifi_probe.py` | OS/firewall/NM wrappers plus `iw list` and `iw reg get` for AP, channel, width, and regulatory candidate policy. | R, but requires `iw`; failures are often reduced to warnings or empty values. | `tests/test_wifi_probe_candidates.py`, `tests/test_iw_phy_parsing.py`, and `tests/test_host_probes.py`. |
| `backend/vr_hotspotd/diagnostics/platform.py` | `/etc/os-release`, executable presence, `systemctl is-active`, root mount, path writability, and session environment. | R. Usually non-root, although namespaces and service-manager access can produce partial facts. NM/firewall service status differs intentionally from functional backend probes. | `tests/test_platform_probes.py` and `tests/test_platform_status.py`. |
| `backend/vr_hotspotd/lifecycle.py` | Reads inventory, uplink, `iw dev`, per-interface `iw info`, NM device state, iwd activity, sysfs driver/bus facts, and live AP state. It also changes NM/iwd ownership, interface state, rfkill, drivers, regulatory domain, engines, tuning, and files. | Mixed R/M and normally runs as root. `_repair_impl`, NM/iwd remediation, interface preparation, `iw reg set`, engine launch, and recovery are outside any read-only snapshot. | `tests/test_active_uplink_start_guard.py`, `tests/test_basic_mode_enforcement.py`, `tests/test_80mhz_enforcement.py`, `tests/test_iwd_ap_reservation.py`, `tests/test_pop_iface_prep_and_timeouts.py`, adapter-normalization, AP-readiness, fallback, Wi-Fi 6, cleanup, and watchdog tests. |
| `backend/vr_hotspotd/engine/channel_scan.py` | `iw dev <ifname> scan` and channel/interference parsing. | A. A scan causes radio activity, can require additional privileges, and may disrupt an interface. It is not a baseline snapshot probe. | Lifecycle/watchdog tests stub selection; there is no direct channel-scan command/parser suite. |
| `backend/vr_hotspotd/engine/tx_power.py` | `iw dev <ifname> info` for current power; `iw dev <ifname> set txpower ...` for changes. | R for `get_tx_power`; M for `set_tx_power`. Dynamic telemetry/tuning stays outside the snapshot. | No direct tx-power probe suite; lifecycle tests stub or avoid these calls. |
| `backend/vr_hotspotd/network_tuning.py` | Re-reads the default uplink, reads ethtool coalescing, then applies/reverts QoS, NAT acceleration, UFW, and coalescing. | Mixed R/M and generally root for changes. Future code may accept the snapshot uplink, but mutation and rollback must remain here. | Default-route compatibility is in `tests/test_host_probes.py`; QoS/UFW have focused tests; there is no snapshot-order test today. |
| `backend/vr_hotspotd/engine/hostapd_nat_engine.py`, `hostapd_bridge_engine.py`, `hostapd6_engine.py` | Re-read default uplink and some firewalld/NM/iwd facts inside a child process; also set regdomain and mutate interfaces, addresses, firewall, and services. | Mixed R/M and root. The child-process boundary is a migration constraint: these modules cannot share an in-memory object without an explicit, characterized input. | Engine bootstrap/recovery, passphrase, iwd, and default-uplink compatibility tests. |

Read-only commands are not guaranteed to succeed without root merely because
they do not mutate. `iw`, netlink, NetworkManager, systemd, and firewall tools
can be missing, namespaced, or permission-restricted. The builder must record
that distinction rather than equating every failure with a negative fact.

### Current operation sequences

The canonical preflight collector currently performs, in broad order:

1. platform probes;
2. firewall, NM, and iwd probes;
3. runtime binary inspection/version probes;
4. adapter inventory (`iw dev`, `iw reg get`, and several phy reads);
5. pure readiness;
6. a separate default-route read;
7. another phy read per concurrency check; and
8. legacy preflight checks.

A lifecycle start currently performs, in broad order:

1. firewall and platform reads;
2. adapter inventory and selection;
3. a default-route read and active-uplink guard;
4. repair and ownership/interface mutations;
5. possible Pop!_OS re-inventory/re-selection after driver or interface churn;
6. regulatory mutation, followed by legacy preflight;
7. another `iw list`/regulatory collection for strict 5 GHz candidates;
8. engine-local default-route/firewall/service reads; and
9. a default-route read by network tuning after engine startup.

The existing active-uplink tests correctly prove that the initial conflict is
blocked before mutation and that a late re-selection is checked before the
replacement adapter is mutated. The later check still compares a refreshed
selection with the earlier uplink observation. The future snapshot design must
make that mixed-generation condition impossible or fail closed; it must not
weaken the landed guard.

## Proposed `HostFactsSnapshot`

### Shape

The names below are design names, not a committed Python or API contract.
Concrete types may live in a future `host_facts.py`, while command execution and
pure parsing remain in `host_probes.py`.

```text
HostFactsSnapshot
  schema_version
  metadata: SnapshotMetadata
  platform: PlatformFacts
  default_uplink: DefaultUplinkFacts
  iw_dev: IwDevFacts
  iw_phys: tuple[IwPhyFacts, ...]
  regulatory: RegulatoryFacts
  network_manager: NetworkManagerFacts
  iwd: IwdFacts
  firewall: FirewallFacts
  adapters: tuple[AdapterFacts, ...]
  probe_records: tuple[ProbeRecord, ...]
  probe_errors: tuple[ProbeError, ...]

SnapshotMetadata
  snapshot_id
  operation_kind
  started_at_utc
  completed_at_utc
  monotonic_duration_ms
  builder_version

ProbeRecord
  probe_id
  source_kind             # command, file, sysfs, environment, derived
  source                  # argv or path, with no secrets
  started_offset_ms
  completed_offset_ms
  exit_status
  timed_out
  missing
  permission_denied
  output_truncated

ProbeError
  probe_id
  kind                    # nonzero, timeout, missing, permission, parse, io
  message                 # bounded and sanitized
  exit_status
```

Normalized fact fields are tri-state where the host can answer true, false, or
unknown. A failed command must not silently become `False`. Compatibility
projections may preserve a current fallback temporarily, but the snapshot must
retain the underlying error.

| Section | Minimum facts |
|---|---|
| Platform | Raw normalized `/etc/os-release` keys, current runtime family/flavor classification, immutable/mutable signals, package-manager-family projection, and the source of each conclusion. |
| Default uplink | Parsed route entries, the first interface selected under current compatibility behavior, command status, and source probe ID. |
| `iw dev` | One pre-mutation capture with interface, phy, type, and association/SSID-presence evidence. Raw upstream SSIDs must not become a public diagnostic field. |
| `iw phy` | One capture per unique phy, with AP-mode evidence, enabled bands/frequencies, 5 GHz channels, VHT/HE width evidence, Wi-Fi 6/HE evidence, and informational AP+managed concurrency. |
| Regulatory | One `iw reg get` capture, global country/header, per-phy country/source/header, and parse errors. |
| NetworkManager | Tool presence, `nmcli` global running result, service activity as a separate fact, and one per-device-state capture when needed by lifecycle. These signals must not be collapsed during collection. |
| iwd | Binary/tool presence, service status/activity, and association evidence already observable from the `iw dev` capture. |
| Firewall | Backend availability, service/functional activity as separate facts, iptables variant, current selected backend/rationale projection, and probe errors. No rule or ownership mutation data is inferred. |
| Adapters | Factual interface/phy/bus/capability/regulatory records derived from the captures. Current inventory and readiness scoring remain separate pure policy projections. |
| Errors and provenance | Every failed, missing, timed-out, permission-denied, malformed, or truncated input, plus snapshot and per-probe timing/source metadata. |

The initial snapshot does not need to absorb every preflight input. `rfkill`
state, address/subnet conflicts, and read-only runtime-binary inspection may be
added as typed facts if PR 1 proves that doing so stays narrow. Otherwise PR 2
can keep them as separately injected preflight inputs. No implementation may
call `_build_engine_env()` from the snapshot builder.

### Builder contract

The builder should:

1. receive an injected command runner, clock, executable resolver, OS-release
   reader, and sysfs reader;
2. record the start time and a unique snapshot ID;
3. resolve tools without installing, enabling, or escalating privileges;
4. capture `iw dev` once, then `iw phy <phy> info` once for each unique phy;
5. capture `iw reg get`, the default route, NM, iwd, firewall, platform, and
   adapter sysfs facts once each;
6. parse and derive normalized immutable facts from those captured inputs;
7. retain independent probe errors and bounded provenance; and
8. record completion time and return the snapshot even when only partial facts
   are available.

The exact choice of `iw phy <phy> info` versus `iw list` must be settled by PR 1
fixtures and parity tests. The preferred starting point is one per-phy command,
because inventory already identifies the phys and current selection policy is
based on that output. A cutover must not occur if a supported platform exposes
materially different data only through `iw list`.

The snapshot must be operation-scoped. Process-wide caching is not part of the
design. Derived views may memoize within the immutable snapshot, but no cache
may survive into a later request/start or hide a newly failed probe.

Raw command output is implementation evidence, not a stable API. Commands need
deadlines and input/output budgets; retained diagnostic excerpts must be
bounded and sanitized. Parsers should receive the complete bounded capture,
while public projections expose curated facts and current response shapes.

## Non-goals

- No same-radio STA+AP support or claim of support. Concurrency remains
  informational evidence.
- No new adapter scoring, recommendation weights, Basic-mode filtering, or
  selection policy.
- No firewall apply, rollback, ownership, or installer mutation changes.
- No installer or uninstaller changes.
- No API schema, route, authentication, CLI, support-bundle, or UI changes.
- No channel-scan, tx-power, telemetry, or post-start AP-readiness redesign.
- No consolidation of mutating commands into `host_probes.py` or the snapshot
  builder.
- No behavior change in the design PR or in the unused-model PR.

## Safety invariants

1. Snapshot collection is read-only. It may use only characterized
   observational commands/files and may never use `sudo`, a service action,
   `iw reg set`, `iw ... disconnect/del/add/set`, `ip ... set/add/del/flush`,
   `nmcli ... set/disconnect`, `iwctl ... disconnect`, `rfkill unblock`, a
   firewall mutation, driver bind/unbind, `modprobe`, or engine start/stop.
2. No mutating command moves into `host_probes.py`, the snapshot model, or its
   builder. Mutation ownership and rollback remain in lifecycle/engine/tuning
   code and are a separate cleanup item.
3. Selection, readiness, preflight evaluation of adapter/uplink/regulatory
   facts, and the active-uplink guard must receive the same `snapshot_id` and
   object generation.
4. The selected AP adapter and the active-uplink guard use the same snapshot.
   Missing or failed safety-critical facts remain explicit and must follow the
   existing fail-closed policy; concurrency evidence never bypasses the guard.
5. Once host mutation starts, lifecycle must not re-probe safety-critical
   adapter, uplink, regulatory, NM/iwd ownership, or firewall-selection facts
   and then use them to revise the current operation's selection. Live probes
   may verify mutation outcomes, but cannot replace the original decision
   evidence.
6. If an adapter disappears or is re-enumerated after mutation, the current
   operation must not silently construct a mixed-generation snapshot. The
   implementation PR must either prove identity against the original factual
   record and retain the original safety decision, or stop/rollback and begin a
   separately identified operation. This is an explicit migration blocker for
   the current Pop!_OS recovery path.
7. Probe failures are data, not hidden exceptions or fabricated negative
   capabilities. Policy layers decide whether an unknown is warning, blocked,
   or a compatibility fallback.
8. Snapshot data is not automatically public. API/report projections remain
   curated, preserve current shapes, and do not expose raw SSIDs, environment
   secrets, unbounded command output, or internal exception text.
9. Tests use fake runners and filesystem fakes. The autouse system-command
   guard and its exact opt-out environment variable remain intact.

## Staged implementation PRs

### PR 1: Introduce the model and builder, unused by runtime

- Add frozen/immutable snapshot, fact, probe-record, and probe-error types.
- Build only from `host_probes` parsers and injected read-only dependencies.
- Add parser fixtures and builder tests for success, missing tools, permission
  failures, timeouts, malformed output, partial phy failures, and bounded
  evidence.
- Assert one `iw dev`, one `iw reg get`, one default-route read, and at most one
  phy-info read per discovered phy.
- Do not import or call the builder from API, diagnostics, inventory,
  readiness, lifecycle, engines, or UI code.

### PR 2: Make preflight consume the snapshot

- Adapt canonical report collection and legacy preflight to accept snapshot
  facts for the overlapping platform, adapter, regulatory, service, firewall,
  and uplink inputs.
- Keep rfkill, subnet, and runtime-binary inputs separately injected unless PR
  1 deliberately typed them.
- Compare old and snapshot-backed report projections from the same captured
  fixtures. Preserve schema version, issue severity/codes/context, actions, and
  API output exactly.
- Use shadow/parity tests before removing any old collector call. Do not make
  lifecycle consume canonical readiness in this PR.

### PR 3: Make adapter inventory and readiness consume the snapshot

- Add a pure compatibility projection from `AdapterFacts` to the existing
  inventory dictionary.
- Keep `_score_adapter()`, readiness scoring, recommendation order, warnings,
  virtual-interface filtering, and public payloads unchanged.
- Characterize inventory's later `Supported interface modes` scan and all
  current unknown/error fallbacks before cutover.
- Remove or bypass process-wide phy caches only after fixture parity proves the
  snapshot projection is equivalent.

### PR 4: Make lifecycle selection consume one operation snapshot

- Build one snapshot before `_repair_impl` or any NM/iwd/interface/regulatory/
  engine/firewall/tuning mutation.
- Select the adapter, validate capabilities, run applicable preflight policy,
  and apply the active-uplink guard from that same snapshot.
- Thread the selected factual inputs through the start attempt; do not refresh
  them after mutation starts.
- Keep post-start AP readiness and telemetry live because they validate
  outcomes, not pre-mutation safety.
- Resolve Pop!_OS re-enumeration and child-engine input strategy explicitly,
  behind parity tests and an intentional cutover. No guard weakening is an
  acceptable compatibility result.

### PR 5: Remove duplicate direct probes where safe

- Remove now-unused preflight/report/inventory/wifi wrappers one group at a
  time.
- Pass the snapshot's chosen uplink to `network_tuning` and, only after
  characterization, across child-engine boundaries instead of re-running the
  default-route command.
- Consolidate read-only service/firewall projections without changing any
  firewall, NM, or iwd mutation and restoration behavior.
- Retain live AP readiness, station telemetry, channel scanning, tx-power
  telemetry, and mutation-result verification.

### PR 6: Update docs and tests after behavior parity is proven

- Remove transition-only adapters and shadow comparisons only after all
  supported fixture matrices and the full suite pass.
- Update architecture/diagnostics docs with the actual final model and command
  budget, not the provisional design names in this document.
- Record manual host validation separately where hardware/kernel behavior
  cannot be represented by unit tests.
- Any intended policy or payload change becomes a separate, explicitly scoped
  PR after the refactor is complete.

## Test strategy

### Pure parser tests

- Table-driven `iw dev`, `iw phy`, `iw list`, regulatory, route, NM, iwd,
  firewall, OS-release, and sysfs fixtures.
- Include multiple phys, multiple default routes, multiple interface-mode
  blocks, decimal frequencies, disabled/no-IR/DFS channels, self-managed phys,
  missing sections, localization/noise, and malformed/truncated output.
- Preserve current exact quirks that are still policy contracts. Do not reuse a
  refactor PR to fix a parser or scoring decision.

### Snapshot builder tests

- Use a fake runner keyed by argv and fake clock/filesystem dependencies.
- Assert the exact read-only allowlist, command count, bounded timeouts/output,
  capture order metadata, immutable result, and unique operation ID.
- Exercise independent timeout, missing-command, permission-denied, nonzero,
  parse-error, and partial-phy outcomes. Verify that successful sibling facts
  survive and every failure is represented in `probe_errors`.
- Assert that no mutating argv can be issued by the builder.

### Parity tests

- Feed old and new projections the same captured fixture, rather than probing a
  live host sequentially, and compare inventory, readiness, canonical report,
  selection, issue, and warning outputs.
- Cover configured versus recommended adapters, no adapters, no AP-capable
  adapters, 2.4/5/6 GHz, 80 MHz, global `00`, self-managed regdomain, missing
  tools, separate Ethernet/Wi-Fi uplinks, and AP-equals-uplink rejection.
- Preserve wrapper-specific error policies until their consumers are migrated
  deliberately.

### Lifecycle ordering tests

- Record events and assert `build snapshot -> select -> active-uplink guard ->
  first host mutation`.
- Assert exactly one builder call and the same `snapshot_id` for selection,
  preflight, readiness, and the guard.
- Fail the test if `get_adapters()`, default-route, regdomain, NM/iwd ownership,
  or firewall-selection probes are called after the mutation boundary.
- Extend the existing late-reselection tests so disappearance cannot mutate a
  replacement adapter based on mixed-generation facts.
- Keep existing engine readiness/retry tests to prove that live outcome
  verification still works.

### Command-safety expectations

- Keep `tests/conftest.py`'s autouse block for `nmcli`, `iw`, `iwctl`,
  `rfkill`, `systemctl`, `firewall-cmd`, `sudo`, `ip`, `iptables`, `nft`,
  `hostapd`, and `dnsmasq` across subprocess and OS execution paths.
- Never set `VR_HOTSPOT_TEST_ALLOW_REAL_SYSTEM_COMMANDS=1` for unit or parity
  tests. Use the existing `mock_missing_system_commands` fixture where a test
  intentionally models absent host tools.
- Prefer behavior assertions on fake-runner calls and outputs over source-text
  assertions.

## Migration risks and mitigations

| Risk | Mitigation and proof required before cutover |
|---|---|
| Performance | Count commands and total duration in builder tests. Use one read per source, bounded deadlines/output, and no global cache. Compare report/start probe counts before and after. |
| Stale facts | Scope snapshots to one operation, record the capture window, never reuse them globally, and keep truly dynamic post-start observations separate. Define a maximum acceptable pre-mutation age before PR 4 cutover. |
| Sequential, not atomic collection | Store per-probe offsets and errors. Use a deterministic order. If the collection window exceeds its budget, return an explicit incomplete/stale result rather than refreshing only some facts. |
| Platform differences | Maintain OS-release and command fixtures for every supported family listed below, plus missing-systemd/namespaced failure cases. Do not infer installer support from runtime family classification. |
| `iw` output variance | Preserve inventory's multi-block AP scan, cover legacy and modern VHT/HE formats, compare `iw list` with per-phy output, and block cutover on unexplained parity differences. |
| Root and permissions | Do not use `sudo` or retry with escalation. Record permission errors separately from absence and unknown capability; validate policy fail-closed behavior. |
| Partial command failures | Keep per-probe results and tri-state facts. One failed phy must not erase other adapters, and a fallback projection must retain the error for diagnostics. |
| Existing process-wide phy caches | Do not let cached Wi-Fi 6/80 MHz results seed a later snapshot. Remove/bypass them only with explicit stale-data and parity tests. |
| Lifecycle ordering | Shadow the snapshot before cutover. Moving preflight ahead of repair or `iw reg set` can change observed results, so ordering changes require focused tests and explicit enablement. |
| Pop!_OS re-enumeration | Treat late adapter identity changes as a design blocker, not a reason to refresh selected facts piecemeal. Prove stable hardware identity or rollback/start a new operation. |
| Child engine processes | Define a bounded, non-secret, validated input for the selected uplink/facts before deleting their direct probes. Do not rely on mutable global state or an implicit environment contract. |
| API/privacy drift | Keep snapshot types internal and use current pure projections. Do not serialize raw command output, upstream SSIDs, paths containing secrets, or exceptions into existing endpoints. |

### Supported-platform parity matrix

| Platform | Snapshot-specific concern |
|---|---|
| SteamOS | Arch-family classification, immutable signals, iwd/tool fallback, and per-adapter association evidence must remain distinguishable. |
| Bazzite | Fedora/rpm-ostree classification and hostapd-NAT selection inputs must remain unchanged; snapshot work does not alter engine choice. |
| CachyOS | Arch-family classification and existing lifecycle timing/vendor preferences remain policy outside the snapshot. |
| EndeavourOS | `ID=endeavouros` plus Arch-like metadata must classify consistently at runtime without changing exact installer routing. |
| Fedora | Mutable Fedora, firewalld service/functional state, and dnf-family projection need fixtures. |
| Ubuntu | Debian-family classification, NM, UFW functional status, and apt-family projection need fixtures. |
| Debian | Debian-family classification must not depend on Ubuntu-specific fields or service availability. |
| Pop!_OS | Ubuntu/Debian family facts must remain stable while late USB interface re-enumeration is handled without a mixed snapshot. |

## Acceptance criteria for the future implementation

- The design PR changes documentation only. PR 1's model/builder is unused by
  runtime and produces no behavior, API, UI, installer, firewall, or adapter
  policy change.
- No runtime consumer is switched until fixture-based old/new parity is proven
  for its full public output and error behavior.
- Inventory and readiness responses, canonical preflight schema/issues,
  lifecycle result codes, adapter scoring/recommendation, and firewall backend
  behavior remain byte/structure compatible unless a later PR explicitly
  authorizes a change.
- One operation creates one snapshot; selection, readiness, preflight, and the
  active-uplink guard can prove they used the same `snapshot_id`.
- The active-uplink guard remains fail closed before any host mutation and is
  covered for configured, recommended, and late-reselection cases.
- Snapshot collection contains no mutating command or artifact creation, and
  no mutating command is moved into `host_probes.py`.
- Errors, unknowns, timeouts, missing tools, permissions, and partial failures
  are represented as data with bounded provenance.
- SteamOS, Bazzite, CachyOS, EndeavourOS, Fedora, Ubuntu, Debian, and Pop!_OS
  fixture matrices pass.
- The full pytest suite passes without real host/system commands. The autouse
  command-safety guard and its tests remain unchanged and green.
- Shell syntax checks, installer matrix validation, `git diff --check`, and all
  existing safety regression tests pass before each staged cutover.
- Direct duplicate probes are removed only after their final consumer is on the
  snapshot and behavior parity has been demonstrated.

## Implementation concerns requiring an explicit decision

Two issues must be resolved in PR 4 rather than assumed away:

1. Pop!_OS recovery can currently re-inventory and select a newly named USB
   interface after repair/interface mutations. An immutable pre-operation
   snapshot cannot safely treat a different interface name as the same adapter
   without stable identity evidence. The safe alternatives are proof of
   identity or rollback plus a new operation.
2. Child engine processes and `network_tuning` currently discover the default
   route themselves. Eliminating that disagreement requires threading the
   already-guarded uplink across process/function boundaries while preserving
   current error behavior. It must not be bundled with firewall mutation or
   engine-policy changes.

Until those decisions and parity tests exist, the snapshot may be introduced
and used by diagnostics/inventory, but lifecycle cutover must remain disabled.
