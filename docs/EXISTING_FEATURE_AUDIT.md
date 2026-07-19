# Existing Feature Audit

Audit date: 2026-07-19  
Branch: `audit-flatpak-preflight-existing-features`  
Method: static repository inspection only; no host networking or service-management commands were run.

## Executive summary

The repository already contains much more than a hotspot script. `vr-hotspotd` is an installed, root systemd service with a loopback HTTP API, a substantial static Web UI, adapter inventory and readiness models, start-time Wi-Fi probing, platform/firewall probes, hostapd/dnsmasq selection, status/log reporting, and a limited sanitized support-bundle download.

The next diagnostics/preflight implementation should **assemble and normalize existing code, not start from scratch**. The current problem is fragmentation: overlapping probes and parsers live in `adapters/inventory.py`, `wifi_probe.py`, `preflight.py`, `diagnostics/platform.py`, `lifecycle.py`, the engine modules, and both installers. At least 21 production Python modules import `subprocess`, with at least 13 local runner helpers plus many direct calls.

**Flatpak work should wait for a cleanup PR.** The host daemon is the right architectural boundary for a sandboxed frontend, but its HTTP contract, privilege boundary, command execution, install ownership, and host-fact model need to be made explicit first. There is no Flatpak, desktop, AppStream, portal, D-Bus, or Unix-socket packaging foundation today.

**General internal-Wi-Fi support is not currently safe to attempt.** The code can discover the default-route interface, but adapter selection does not exclude it. Start-up may unmanage and disconnect the selected radio. An AP+managed concurrency parser exists, but it is unused by selection or start-up. Internal use may work when a separate Ethernet uplink exists, but that condition is neither modeled nor enforced.

Recommended next PR: a behavior-preserving **host-probe consolidation PR** covering read-only command execution, shared wireless/OS/firewall parsing, and characterization tests. It should add no new diagnostic surface or packaging feature.

## Existing architecture and installation boundary

| Item | Existing implementation |
|---|---|
| Daemon | `backend/vr_hotspotd/main.py`, packaged as the `vr-hotspotd` entry point in `pyproject.toml`. |
| Service | `backend/systemd/vr-hotspotd.service`; the backend installer writes it to `/etc/systemd/system/vr-hotspotd.service`. No `User=` is set, so the system service runs as root. |
| Installed application | `backend/scripts/install.sh` copies the repository payload to `/var/lib/vr-hotspot/app` by default and creates `/var/lib/vr-hotspot/venv`. |
| Config/state | Environment and API token: `/etc/vr-hotspot/env`; persisted config: `/var/lib/vr-hotspot/config.json`; runtime state: `/run/vr-hotspot/state.json`. |
| Helper boundary | Autostart helper scripts are installed under `/var/lib/vr-hotspot/bin`; `vr-hotspot-autostart.service` calls the daemon through HTTP. |
| Host API | `ThreadingHTTPServer` on `127.0.0.1:8732` by default, with `/healthz` and authenticated `/v1/*` routes. A non-loopback bind is rejected unless an API token exists. |
| Frontend relationship | The daemon serves `assets/index.html`, JavaScript, CSS, and images itself. The browser UI calls the daemon's `/v1/*` API. |
| CLI relationship | There is no user-facing client CLI. The `vr-hotspotd` console script starts the daemon; shell helpers and documented `curl` commands are the only CLI-to-daemon clients. `tools/platform_probe.py` probes the host directly instead of using the daemon. |
| Other IPC | No project-owned D-Bus API or Unix-domain socket exists. The only service API is HTTP/TCP. |

One install-layout comment in `vr-hotspotd.service` describes a release symlink under `app/releases/<ver>`, but the current installer copies directly into `/var/lib/vr-hotspot/app`; that release-switching model is not implemented.

## Already exists

| Capability | Evidence | Current use |
|---|---|---|
| Physical adapter inventory | `adapters/inventory.py:get_adapters()` parses `iw dev`, per-phy capabilities, regulatory data, and sysfs bus type; virtual interfaces are filtered. | `/v1/adapters`, start/repair selection, support bundle. |
| AP-mode detection | `inventory._phy_supports_ap()` and `wifi_probe._parse_supported_interface_modes()` parse supported interface modes. | Recommendation and 5 GHz start gating. |
| Band detection | Inventory derives enabled 2.4, 5, and 6 GHz frequencies. | Selection, readiness, and band validation. |
| 80 MHz detection and enforcement | Inventory parses VHT/HE hints; `wifi_probe` constructs regulatory-aware 80 MHz blocks; lifecycle rejects incapable 5 GHz adapters and verifies actual post-start width. | Basic Mode and strict 5 GHz start path. |
| Wi-Fi 6 adapter detection | Inventory checks HE interface types and 802.11ax/HE markers; lifecycle gates the `--wifi6` engine flag on the selected adapter. | Adapter API and start path. |
| Adapter readiness model | `adapters/readiness.py` provides scores, reason codes, Basic Mode visibility, 6 GHz states, and recommendations as a pure model. | `/v1/adapters/readiness` and Basic/Pro Web UI. |
| Firewall/backend detection | `wifi_probe.detect_firewall_backends()` distinguishes active firewalld/ufw and available nftables/iptables, including nft-vs-legacy iptables. | Start-time backend selection. |
| NetworkManager/iwd handling | Lifecycle detects NM ownership, attempts `managed no`, reserves a SteamOS+iwd AP interface, and rejects an interface that remains associated. | Start-time remediation and SteamOS path. |
| Platform matrix | `diagnostics/platform.py` reports OS release, immutability signals, writable paths, display session, systemd, NetworkManager, and firewall presence/activity. | Included in `/v1/status`. |
| Runtime status and logs | `/v1/status`, optional live engine tails, capture-log collection, state reconciliation, and bounded supervisor output exist. | Web UI and manual support. |
| Limited support bundle | Authenticated `/v1/diagnostics/support_bundle`, ZIP assembly, manifest metadata, redaction, command/file collector primitives, and a Pro UI download action exist. | Exports version, status, inventory, and readiness JSON. |
| Other diagnostics | Connected-client snapshots, ping, ping-under-load, UDP latency, and telemetry exist. | Authenticated API and Web UI. |
| hostapd/dnsmasq selection | `engine/supervisor.py` resolves system and bundled binaries, honors force/preference policy, tests bundled dnsmasq execution, and compares hostapd HT/VHT config support. | Engine environment construction and fallback selection. |
| Test command safety | `tests/conftest.py` blocks real `nmcli`, `iw`, `iwctl`, `rfkill`, `systemctl`, firewall, IP, hostapd, and dnsmasq execution across common subprocess/os paths. | Autouse for pytest; its escape hatch is explicitly tested. |

## Partially exists

| Capability | What is present | Why it is incomplete |
|---|---|---|
| Unified preflight | `wifi_probe.probe()` checks OS/firewall/NM plus strict 5 GHz/AP/80 MHz/channel/regdomain facts. `preflight.run()` checks rfkill, regdomain, hostapd SAE/HE, subnet conflicts, bridge uplink existence, and internet-disabled state. | They are separate models with separate runners and repeated `iw` parsing. `preflight.run()` is start-only, has no API surface, and its returned errors are recorded as warnings rather than stopping the general start path. |
| Wi-Fi 6/6E readiness | Adapter HE detection, 6 GHz frequency checks, hostapd HE/SAE preflight, WPA3 gating, and readiness states exist. | The readiness model can report `blocked_by_hostapd`, but normal inventory never supplies `hostapd_6ghz_capable`; the API therefore cannot derive that state. No end-to-end test covers hostapd HE rejection. |
| hostapd validation | Supervisor uses `hostapd -t` with a generated HT/VHT config; preflight inspects `hostapd -v/-vv` output for SAE/HE. | The probes are separate, feature inference is heuristic, and preflight results are not a consistent gate. |
| dnsmasq validation/fallback | Bundled dnsmasq is checked with `--version`; unusable bundled dnsmasq can fall back to the system binary. | System dnsmasq is generally existence-checked only, and no unified preflight result exposes the chosen binary and validation outcome. |
| Support export | ZIP, redaction, manifest, endpoint, UI, and generic collector primitives are implemented. | The endpoint does not use the command collector and omits the documented systemd status/journal, OS/kernel files, wireless commands, firewall output, NM status, and redacted config. No CLI exporter exists. |
| Active-uplink detection | Four Python `_default_uplink_iface()` implementations and two installer implementations parse the default route. | The result is used for NAT/firewall/tuning after adapter choice, not to protect the uplink during adapter recommendation or start. No same-interface guard exists. |
| Internal-adapter guidance | Inventory detects USB vs PCI, scores USB higher, penalizes `wlan0`, and readiness hides `wlan0` when an external AP-capable adapter exists. README recommends USB. | Interface name is used as a proxy for role. If no USB adapter exists, an internal adapter can be selected with no explicit consent or uplink safety decision. The Basic Mode success reason is named `usb_5ghz_80mhz_ap` even when the accepted adapter is not USB. |
| STA+AP concurrency knowledge | `lifecycle._parse_ap_managed_concurrency()` parses `iw phy` valid-interface combinations and has unit tests. | It has no production caller. There is no channel-count check, role plan, same-radio orchestration, or preservation of the STA connection. |
| Platform support | Exact distro cases, several runtime overrides, vendor profiles, and a platform probe exist. | Installer, runtime, diagnostics, and vendor-selection classifiers do not share one family model. Exact `ID` handling rejects some otherwise recognizable derivatives. |
| Application identity assets | `pyproject.toml` has Python package metadata; `assets/logo.png` and `assets/favicon.svg` exist. | These are package/Web UI assets, not freedesktop application metadata or an installed icon theme. |

## Missing

| Missing capability | Impact |
|---|---|
| Shared command runner/executor contract | Timeouts, environment, stderr, missing-command behavior, privilege expectations, and result types vary by module; this complicates a clean host/sandbox boundary. |
| Single normalized host-facts snapshot | Adapter, regulatory, OS, firewall, NM, uplink, and binary facts are repeatedly probed and can disagree within one operation. |
| Public, side-effect-free preflight contract | There is no endpoint or CLI that returns one authoritative readiness result before start. |
| Uplink-aware adapter role policy | No enforced `AP adapter != active uplink` rule, Ethernet-vs-Wi-Fi role model, explicit override, or safe failure result exists. |
| Implemented same-radio STA+AP support | Capability parsing alone does not preserve an uplink or create a supported concurrent interface plan. |
| Full support-bundle collection | The documented command/file collectors are not wired into the API export. |
| User-facing client CLI | There is no `vr-hotspot status/start/preflight/diagnostics` client that talks to the daemon. |
| Flatpak packaging | No manifest, finish-args, runtime/SDK selection, build definition, or host-service installation/update story exists. |
| Desktop integration | No `.desktop` file, AppStream/metainfo XML, installed application icon set, MIME/URL handler, or native GUI wrapper exists. |
| D-Bus or Unix-socket API | None exists; adopting one would be a new API, not discovery of an existing implementation. |
| RHEL-family installer support | RHEL, CentOS Stream, Rocky, AlmaLinux, and other `ID_LIKE`-only Fedora/RHEL systems are rejected by `install.sh`. |
| Non-systemd support | The backend installer explicitly requires `systemctl`. |

## Duplicated/scattered logic

| Logic | Locations | Cleanup concern |
|---|---|---|
| Command execution | Local runners in inventory, clients, platform, firewalld, three hostapd engines, ufw, lifecycle, NAT/QoS, preflight, and wifi probe; direct subprocess calls in additional diagnostics/tuning modules. | At least 21 production Python modules import `subprocess`; there is no general runner. `support_bundle.collect_command()` is injectable but is collector-specific and unused elsewhere. |
| `iw` executable lookup/parsing | `adapters/inventory.py`, `wifi_probe.py`, `lifecycle.py`, diagnostics clients, channel scan, tx power, and system tuning. | Different fallbacks, timeouts, parse shapes, and error defaults. |
| AP-mode parsing | Inventory, wifi probe, and lifecycle. | Similar input can produce `False`, `None`, or a hard failure depending on caller. |
| Regulatory parsing | Inventory and wifi probe, plus lifecycle regdom mutation and preflight validation. | Duplicate parsers and separate policy decisions. |
| Adapter scoring/readiness | `inventory._score_adapter()` and `adapters/readiness.py`. | The two models differ: inventory always penalizes `wlan0`; readiness penalizes it only when USB is present. |
| OS release/family detection | `os_release.py`, `diagnostics/platform.py`, `wifi_probe.detect_os_flavor()`, `vendor_paths.py`, `engine/supervisor.py`, and `install.sh`. | Supported-family answers can differ between install, diagnostics, and runtime. |
| Default uplink detection | Three hostapd engines, `network_tuning.py`, and both installer layers. | No shared result and no adapter-role safety check. |
| NetworkManager/iwd control | `lifecycle.py` and `engine/hostapd_nat_engine.py`, plus hard-coded operator scripts under `tools/`. | Duplicate disconnect/unmanage behavior and restoration rules; tools assume `wlan0`/`wlan1`. |
| Firewall detection/application | `wifi_probe.py`, `diagnostics/platform.py`, hostapd engines, supervisor/firewalld, network tuning/ufw/NAT/QoS, and installers. | Presence, activity, chosen backend, runtime changes, and permanent installer changes are modeled separately. |
| hostapd capability probing | `preflight.py` and `engine/supervisor.py`. | SAE/HE and HT/VHT are split into incompatible result shapes. |
| Installer firewalld forwarding | `install.sh` and `backend/scripts/install.sh`. | Near-duplicate default-route, zone, masquerade, and forward logic can run twice in the normal install flow. |
| Log/secret redaction | API status helpers and `diagnostics/support_bundle.py`. | Status replacement and bundle-wide structured redaction have different coverage. |

## Platform support

| Platform | Static support found | Assessment |
|---|---|---|
| SteamOS | Exact installer case; immutable-base dependency check; forced strict bundled stack; firewalld path; SteamOS+iwd reservation/disconnect handling. | Supported and specialized, but hardware/manual validation is still important. |
| CachyOS | Exact pacman case; bundled-binary preference; longer AP-ready timeout and interface-up grace; dnsmasq fallback plan. | Supported and specialized. |
| Arch | Exact pacman case and generic Arch vendor profile. | Supported generically; little Arch-specific pytest coverage. |
| EndeavourOS | Exact pacman case; bundled-hostapd/system-dnsmasq validation; installer firewalld forwarding. | Supported; this is the best-characterized installer derivative. |
| Fedora | Exact dnf case; firewalld integration and generic runtime probing. | Supported generically; dependency and runtime behavior have limited direct tests. |
| Ubuntu/Debian | Exact apt cases install hostapd/dnsmasq; Pop!_OS has additional readiness/recovery behavior. | Ubuntu, Debian, and Pop are declared supported. Apt-plan and plain Ubuntu/Debian behavior need direct tests. |
| Bazzite | Exact rpm-ostree case; hostapd-NAT mode and vendor preference/forcing. | Supported but internally inconsistent: the top installer looks only for a profile-specific `vendor/bin/bazzite/hostapd` (absent in this tree), while the backend installer later forces the base bundled stack. |
| Fedora Atomic variants | Runtime diagnostics can label Fedora Atomic and detect rpm-ostree. | Installer still follows the exact `fedora` dnf path unless the ID is `bazzite`; generic Atomic handling is not implemented. |
| RHEL-like systems | Some runtime feature probes may work. | Installer rejects their IDs; unsupported. |
| Other derivatives | `wifi_probe` recognizes Linux Mint and can infer Arch/Fedora families through tokens. | Installer ignores `ID_LIKE` for package-manager selection, so these are unsupported unless their exact ID is listed. |

## Existing test coverage and gaps

| Requested area | Existing coverage | Important gaps |
|---|---|---|
| Adapter/AP detection | `test_adapter_inventory.py`, `test_adapter_inventory_virtual.py`, `test_adapter_readiness.py`, API readiness tests. | No realistic combined multi-phy snapshot test across inventory, wifi probe, and readiness; limited bus-role coverage. |
| Preflight | Lifecycle tests frequently stub `preflight.run`; wifi candidate tests cover a portion of strict 5 GHz probing. | No direct tests for rfkill, subnet conflict, bridge uplink, hostapd SAE/HE, or the aggregate `preflight.run()` result/gating behavior. |
| 80 MHz | `test_wifi_probe_candidates.py`, `test_80mhz_enforcement.py`, Basic Mode and post-start width tests. | HE80 parsing and cross-parser consistency are not directly characterized. |
| Wi-Fi 6/6E | Inventory HE-marker tests, `test_wifi6_gating.py`, readiness regdomain case. | No hostapd-HE negative path, SAE capability test, 6 GHz start matrix, or API `blocked_by_hostapd` integration test. |
| Package-manager selection | CI runs `tools/ci/install_matrix_check.sh` for Arch, CachyOS, EndeavourOS, SteamOS, Ubuntu, Fedora, and Bazzite. Pytest covers EndeavourOS, SteamOS plan separation, CachyOS fallback, and rpm-ostree retry behavior. | No exact apt/dnf/Bazzite dependency assertions; Debian and Pop are absent from the CI matrix; no RHEL-like rejection/family test. |
| firewalld | `test_firewalld_change_interface.py` and installer tests for runtime/permanent forwarding. | No complete supervisor apply/cleanup test, backend detection-priority test, failure matrix, or duplicate-installer-call test. |
| ufw | `test_ufw.py` covers revert ordering and missing-rule tolerance. | Apply, active detection, backend selection, and Ubuntu integration are not covered end to end. |
| hostapd/dnsmasq fallback | `test_supervisor_env.py` covers bundled dnsmasq acceptance/rejection and missing dnsmasq; installer fallback tests exist. | Direct hostapd HT/VHT probe tests, system-dnsmasq validation, selected-binary reporting, and start fallback across real result shapes are missing. |
| iwd | `test_iwd_ap_reservation.py` covers per-interface config, disconnect order, and still-associated failure. | Restoration/removal, non-SteamOS iwd, and uplink-role protection are missing. |
| NetworkManager | Basic Mode and Pop!_OS suites cover ownership gates, auto-remediation, disconnect handling, and interface recovery. | No assertion prevents unmanaging the active uplink. |
| SteamOS/platform | SteamOS installer separation and iwd behavior are tested; `test_platform_status.py` verifies matrix inclusion. | The platform matrix's OS, immutability, service, and firewall probes are not directly tested; SteamOS end-to-end preflight is absent. |
| Logs/support bundle | Status tails, capture logs, archive, manifest, collectors, redaction, API auth/download, and UI contract have strong unit coverage. | The API does not wire full collectors, so there are no integration tests for journal/systemd/wireless/firewall collection or a CLI exporter. |
| Internal Wi-Fi/same-radio | One readiness test deprioritizes internal `wlan0` when USB is present; concurrency parser unit tests exist. | No active-uplink selection test, no `AP == uplink` rejection test, no internal-only consent/safety test, and no STA+AP orchestration test. |
| Test safety | `test_system_command_guard.py` verifies all guarded execution paths and the explicit escape hatch. | Keep this guard unchanged while runner consolidation proceeds. |

## Recommended cleanup order

1. Add characterization tests for current preflight aggregation, firewall priority, binary selection, platform-family decisions, default-route parsing, and the dangerous `AP == uplink` case.
2. Introduce one bounded, injectable command-result abstraction for **read-only probes first**; preserve all existing API payloads and lifecycle behavior.
3. Consolidate `iw`, regulatory, OS-family, firewall, NetworkManager, binary, and uplink facts into one per-operation host snapshot. Keep parsers pure.
4. Replace interface-name heuristics with explicit adapter roles: bus, active uplink, current association, candidate AP, and concurrency evidence. Fail closed when a selected AP is the uplink unless a future, proven concurrent plan explicitly allows it.
5. Move mutating engine, firewall, tuning, and installer command execution behind explicit host-side executors with auditable privilege and rollback behavior.
6. Align installer and runtime platform classification, remove duplicate firewalld forwarding, and resolve the documented-vs-actual install layout.
7. Wire the already-built support-bundle primitives and normalized facts into one preflight/diagnostics response and export path.
8. Only then add freedesktop metadata and a Flatpak frontend that talks exclusively to the existing host daemon API.

## Recommended next PR

Create a cleanup-only PR named along the lines of **“Consolidate read-only host probes”**:

- add a small injected runner with a normalized result (`argv`, exit status, stdout/stderr, timeout, missing/permission state);
- use it only in `adapters/inventory.py`, `wifi_probe.py`, `preflight.py`, and `diagnostics/platform.py`;
- extract shared pure `iw`/regulatory and OS/firewall parsers;
- collect one facts snapshot per start or diagnostic request;
- preserve `/v1/adapters`, `/v1/adapters/readiness`, `/v1/status`, and lifecycle result shapes;
- add the direct preflight/firewall/platform/uplink characterization tests listed above;
- add no Flatpak manifest, daemon, endpoint, GUI feature, or internal-radio behavior.

This creates a controlled seam for later daemon and Flatpak work without mixing packaging with behavioral changes.

## Explicit decisions

- **Diagnostics/preflight:** assemble from existing inventory, readiness, wifi probe, preflight, platform matrix, status/log, and support-bundle code. Do not reimplement it from scratch.
- **Flatpak:** wait. Start only after the read-only host boundary is consolidated, API ownership is documented, and host installation/update responsibilities are decided.
- **Internal Wi-Fi:** do not advertise or expand support yet. First add uplink-aware role selection, fail-closed guards, current-association facts, used concurrency validation, explicit user consent, restoration behavior, and tests. A separate Ethernet uplink can become the first narrowly supported internal-radio scenario.

## Verification

- `.venv/bin/python -m pytest -q`: **262 passed, 4 subtests passed** in 11.43 seconds.
- The pytest system-command safety guard remained intact.
