"""Canonical, serializable preflight diagnostics report.

The collector composes existing read-only probes.  The report builder is pure
so CLI, HTTP, support-bundle, and future UI callers can share one contract.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from vr_hotspotd import host_probes, preflight
from vr_hotspotd.adapters.inventory import (
    get_adapters,
    probe_ap_managed_concurrency,
)
from vr_hotspotd.adapters.readiness import build_readiness_model
from vr_hotspotd.config import DEFAULT_CONFIG
from vr_hotspotd.diagnostics.platform import collect_platform_matrix
from vr_hotspotd.engine import supervisor
from vr_hotspotd.policy import ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
from vr_hotspotd.vendor_paths import vendor_bin_dirs


REPORT_SCHEMA_VERSION = 1

_HOSTAPD_VERSION_RE = re.compile(r"\bhostapd\s+v?([^\s]+)", re.IGNORECASE)
_DNSMASQ_VERSION_RE = re.compile(r"\bdnsmasq\s+version\s+([^\s]+)", re.IGNORECASE)

_ISSUE_MESSAGES = {
    "adapter_inventory_unavailable": "The Wi-Fi adapter inventory could not be collected.",
    "no_wifi_adapter": "No physical Wi-Fi adapter was detected.",
    "no_ap_capable_adapter": "Wi-Fi adapters were detected, but none supports AP mode.",
    "configured_adapter_not_found": "The configured AP adapter is not present in the current inventory.",
    "ap_mode_unavailable": "The selected Wi-Fi adapter does not support AP mode.",
    "ap_mode_unknown": "AP-mode support could not be determined for the selected Wi-Fi adapter.",
    "configured_5ghz_unavailable": "The selected Wi-Fi adapter does not support the configured 5 GHz band.",
    "configured_6ghz_unavailable": "The selected Wi-Fi adapter does not support the configured 6 GHz band.",
    "configured_80mhz_unavailable": "The selected Wi-Fi adapter does not support the configured 80 MHz channel width.",
    "hostapd_not_available": "No executable hostapd binary was found by read-only inspection.",
    "dnsmasq_not_available": "No executable dnsmasq binary was found by read-only inspection.",
    "hostapd_version_unknown": "hostapd is available, but its version could not be determined.",
    "dnsmasq_version_unknown": "dnsmasq is available, but its version could not be determined.",
    "binary_selection_failed": "The configured binary selection constraints could not be satisfied by read-only inspection.",
    "firewall_backend_not_detected": "No supported firewall backend was detected for internet sharing.",
    "default_uplink_not_detected": "No active default-route interface was detected for internet sharing.",
    ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK: "The selected AP adapter is also the active uplink; VRhotspot does not yet support using one radio for both roles safely.",
    "platform_family_unknown": "The Linux distribution family could not be classified.",
    "rfkill_blocked": "A Wi-Fi radio is blocked by rfkill.",
    "rfkill_not_found": "rfkill is unavailable, so radio block state could not be checked.",
    "rfkill_list_failed": "The Wi-Fi radio block state could not be read.",
    "regdom_unknown_or_global_00": "The wireless regulatory domain is unknown or set to the global 00 domain.",
    "regdom_mismatch": "The configured country does not match the adapter's effective regulatory domain.",
    "hostapd_missing_sae": "The selected hostapd does not report WPA3-SAE support required by the configuration.",
    "hostapd_sae_unknown": "WPA3-SAE support could not be determined for the selected hostapd.",
    "hostapd_missing_11ax": "The selected hostapd does not report Wi-Fi 6/802.11ax support required by the configuration.",
    "hostapd_11ax_unknown": "Wi-Fi 6/802.11ax support could not be determined for the selected hostapd.",
    "subnet_conflict": "The configured hotspot subnet conflicts with an existing address or route.",
    "ip_addr_check_failed": "Existing IPv4 addresses could not be checked for hotspot subnet conflicts.",
    "ip_route_check_failed": "Existing IPv4 routes could not be checked for hotspot subnet conflicts.",
    "gateway_ip_invalid_for_preflight": "The configured hotspot gateway address could not be validated.",
    "bridge_uplink_not_found": "The configured bridge uplink interface does not exist.",
    "internet_disabled_no_nat": "Internet sharing is disabled for the configured hotspot.",
    "probe_unavailable": "A read-only diagnostics probe did not complete.",
}

_ACTION_MESSAGES = {
    "adapter_inventory_unavailable": "Verify that iw is installed and that the daemon can read wireless capabilities.",
    "no_wifi_adapter": "Connect a Wi-Fi adapter that supports AP mode, 5 GHz, and 80 MHz channels.",
    "no_ap_capable_adapter": "Use a Wi-Fi adapter and driver that report AP-mode support.",
    "configured_adapter_not_found": "Reconnect the configured adapter or choose one currently reported by VRhotspot.",
    "ap_mode_unavailable": "Use a Wi-Fi adapter and driver that support AP mode.",
    "ap_mode_unknown": "Verify the adapter's AP-mode capability with a supported driver and iw.",
    "configured_5ghz_unavailable": "Choose a 5 GHz-capable adapter or select a supported band in Advanced Mode.",
    "configured_6ghz_unavailable": "Choose a 6 GHz AP-capable adapter or select a supported band.",
    "configured_80mhz_unavailable": "Use an 80 MHz-capable adapter or explicitly enable the existing 40 MHz fallback in Advanced Mode.",
    "hostapd_not_available": "Install a compatible hostapd or restore the VRhotspot bundled binary selected for this platform.",
    "dnsmasq_not_available": "Install dnsmasq or restore the VRhotspot bundled binary selected for this platform.",
    "hostapd_version_unknown": "Run the selected hostapd with -v and review its installation if version probing fails.",
    "dnsmasq_version_unknown": "Run the selected dnsmasq with --version and review its installation if version probing fails.",
    "binary_selection_failed": "Review VR_HOTSPOT_FORCE_SYSTEM_BIN, VR_HOTSPOT_FORCE_VENDOR_BIN, and the installed binaries.",
    "firewall_backend_not_detected": "Install or enable a supported firewall backend before sharing the uplink.",
    "default_uplink_not_detected": "Connect the host to an upstream network or disable internet sharing.",
    ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK: "Use a separate Wi-Fi adapter for the AP, or use Ethernet or another interface as the uplink.",
    "platform_family_unknown": "Confirm that this distribution is supported and that /etc/os-release is readable.",
    "rfkill_blocked": "Unblock the Wi-Fi radio with the host's normal radio or rfkill controls.",
    "rfkill_not_found": "Install rfkill if radio-block diagnostics are needed.",
    "rfkill_list_failed": "Check rfkill permissions and retry the diagnostics report.",
    "regdom_unknown_or_global_00": "Set a valid two-letter country code and verify the effective regulatory domain.",
    "regdom_mismatch": "Align the configured country with the adapter's effective regulatory domain.",
    "hostapd_missing_sae": "Use a hostapd build with SAE support or select WPA2 on a non-6 GHz band.",
    "hostapd_sae_unknown": "Verify that the selected hostapd build supports SAE before using WPA3 or 6 GHz.",
    "hostapd_missing_11ax": "Use a hostapd build with 802.11ax/HE support or select a non-6 GHz band.",
    "hostapd_11ax_unknown": "Verify that the selected hostapd build supports 802.11ax/HE before using 6 GHz.",
    "subnet_conflict": "Choose a hotspot subnet that does not overlap an existing address or route.",
    "ip_addr_check_failed": "Verify that the ip utility is installed and readable, then retry.",
    "ip_route_check_failed": "Verify that the ip utility is installed and readable, then retry.",
    "gateway_ip_invalid_for_preflight": "Set a valid IPv4 hotspot gateway address.",
    "bridge_uplink_not_found": "Choose an existing bridge uplink interface.",
    "internet_disabled_no_nat": "Enable internet sharing if clients should reach the active uplink.",
    "probe_unavailable": "Retry the report after checking the daemon logs and required host utilities.",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _normalize_band(value: Any) -> str:
    normalized = str(value or "5ghz").strip().lower()
    if normalized in ("2", "2g", "2.4", "2.4g", "2.4ghz"):
        return "2.4ghz"
    if normalized in ("6", "6g", "6e", "6ghz"):
        return "6ghz"
    return "5ghz"


def _package_manager_family(
    os_family: Optional[str],
    os_flavor: str,
    immutable: bool,
) -> Optional[str]:
    if os_flavor == "bazzite" or (
        os_flavor == "fedora_atomic" and immutable
    ):
        return "rpm-ostree"
    if os_family == "arch":
        return "pacman"
    if os_family == "debian":
        return "apt"
    if os_family == "fedora":
        return "rpm-ostree" if immutable else "dnf"
    return None


def _service_status(*, present: bool, active: bool, reported: Any = None) -> str:
    if not present:
        return "not_installed"
    if active:
        return "active"
    if isinstance(reported, str) and reported.strip() not in ("", "unknown"):
        return reported.strip().lower()
    return "inactive"


def _firewall_view(firewall: Mapping[str, Any]) -> Dict[str, Any]:
    details = _as_dict(firewall)
    backend = str(details.get("selected_backend") or "unknown")
    if backend == "firewalld":
        active = bool(_as_dict(details.get("firewalld")).get("active"))
        status = "active" if active else "available"
    elif backend == "ufw":
        active = bool(_as_dict(details.get("ufw")).get("active"))
        status = "active" if active else "available"
    elif backend in ("nftables", "iptables"):
        status = "available"
    else:
        status = "not_detected"
    return {
        "backend": backend,
        "status": status,
        "rationale": details.get("rationale"),
    }


def _binary_view(value: Any) -> Dict[str, Any]:
    item = _as_dict(value)
    path = item.get("path") if isinstance(item.get("path"), str) else None
    capabilities = _as_dict(item.get("capabilities"))
    return {
        "available": bool(item.get("available") or path),
        "source": item.get("source"),
        "path": path,
        "version": item.get("version"),
        "capabilities": capabilities,
        "probe_error": item.get("probe_error"),
    }


def _find_adapter(inventory: Mapping[str, Any], ifname: Optional[str]) -> Optional[Dict[str, Any]]:
    if not ifname:
        return None
    for item in _as_list(inventory.get("adapters")):
        adapter = _as_dict(item)
        if adapter.get("ifname") == ifname or adapter.get("interface") == ifname:
            return adapter
    return None


def _report_adapter_name(inventory: Mapping[str, Any], config: Mapping[str, Any]) -> Optional[str]:
    configured = config.get("ap_adapter")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    recommended = inventory.get("recommended")
    return str(recommended) if recommended else None


def _stable_issue(code: Any) -> Tuple[str, Dict[str, str]]:
    """Separate legacy parameterized issue strings into code and context."""

    reported = str(code or "unknown_issue").strip()
    match = re.match(r"^([a-z][a-z0-9_]*)(?:\((.*)\)|:(.*))?$", reported)
    if not match:
        return "unknown_issue", {"reported": reported}

    stable_code = match.group(1)
    context: Dict[str, str] = {}
    parameters = match.group(2)
    detail = match.group(3)
    if parameters:
        for raw_parameter in parameters.split(","):
            key, separator, value = raw_parameter.partition("=")
            if separator and key.strip():
                context[key.strip()] = value.strip()
    elif detail:
        context["detail"] = detail.strip()

    if stable_code == "regdom_mismatch":
        adapter_country = context.pop("adapter", None)
        configured_country = context.pop("config", None)
        if adapter_country:
            context["adapter_country"] = adapter_country
        if configured_country:
            context["configured_country"] = configured_country
    return stable_code, context


def _fallback_issue_message(code: str) -> str:
    readable = code.replace("_", " ").replace(":", ": ").strip()
    if readable:
        readable = readable[0].upper() + readable[1:]
    return f"Preflight check reported: {readable}."


def _preflight_issue_context(
    code: str,
    details: Mapping[str, Any],
) -> Dict[str, Any]:
    if code == "regdom_mismatch":
        regdom = _as_dict(details.get("regdom"))
        return {
            "adapter_country": regdom.get("adapter_country"),
            "configured_country": regdom.get("cfg_country"),
            "global_country": regdom.get("global_country"),
        }
    if code == "rfkill_blocked":
        return {"blocked_devices": _as_list(_as_dict(details.get("rfkill")).get("blocked"))}
    if code == "subnet_conflict":
        return {"conflicts": _as_list(_as_dict(details.get("subnet")).get("conflicts"))}
    return {}


def _normalize_probe_failures(value: Any) -> List[Dict[str, str]]:
    failures: List[Dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        probe = str(item.get("probe") or "unknown")
        error = str(item.get("error") or "unknown error")
        failures.append({"probe": probe, "error": error})
    return failures


def build_preflight_report(
    *,
    platform_matrix: Mapping[str, Any],
    firewall: Mapping[str, Any],
    network_manager: Mapping[str, Any],
    iwd: Mapping[str, Any],
    binaries: Mapping[str, Any],
    inventory: Mapping[str, Any],
    readiness: Mapping[str, Any],
    active_uplink_interface: Optional[str],
    concurrency_by_phy: Mapping[str, Optional[bool]],
    existing_preflight: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    probe_failures: Optional[List[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the v1 report from already-collected probe results only."""

    cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
    if isinstance(config, Mapping):
        cfg.update(config)

    platform_data = _as_dict(platform_matrix)
    os_data = _as_dict(platform_data.get("os"))
    immutability = _as_dict(platform_data.get("immutability"))
    os_classification = host_probes.classify_os_flavor(
        {
            "id": os_data.get("id"),
            "id_like": os_data.get("id_like"),
            "variant_id": os_data.get("variant_id"),
            "version_id": os_data.get("version_id"),
            "name": os_data.get("pretty_name") or os_data.get("name"),
        }
    )
    os_family = os_classification.get("family")
    os_flavor = str(os_classification.get("flavor") or "unknown")
    is_steamos = os_flavor == "steamos"
    is_immutable = bool(immutability.get("is_immutable"))
    os_known = bool(os_data.get("id") or os_data.get("pretty_name") or os_family)
    if is_steamos:
        host_kind = "steamos"
    elif is_immutable:
        host_kind = "immutable_linux"
    elif os_known:
        host_kind = "mutable_linux"
    else:
        host_kind = "unknown"

    integration = _as_dict(platform_data.get("integration"))
    platform_nm = _as_dict(integration.get("network_manager"))
    nm_data = _as_dict(network_manager)
    nm_present = bool(
        platform_nm.get("present")
        or nm_data.get("present")
        or nm_data.get("nmcli")
    )
    nm_active = bool(
        platform_nm.get("active")
        or nm_data.get("active")
        or nm_data.get("running")
    )
    iwd_data = _as_dict(iwd)
    iwd_present = bool(iwd_data.get("present") or iwd_data.get("iwctl"))
    iwd_active = bool(iwd_data.get("active"))

    inventory_data = _as_dict(inventory)
    readiness_data = _as_dict(readiness)
    readiness_by_interface = {
        str(item.get("interface")): item
        for item in _as_list(readiness_data.get("adapters"))
        if isinstance(item, Mapping) and item.get("interface")
    }
    concurrency = _as_dict(concurrency_by_phy)
    report_adapter = _report_adapter_name(inventory_data, cfg)
    configured_adapter = cfg.get("ap_adapter")
    configured_adapter = (
        configured_adapter.strip()
        if isinstance(configured_adapter, str) and configured_adapter.strip()
        else None
    )

    adapters: List[Dict[str, Any]] = []
    for raw_item in _as_list(inventory_data.get("adapters")):
        item = _as_dict(raw_item)
        ifname = str(item.get("ifname") or item.get("interface") or item.get("id") or "unknown")
        phy = item.get("phy")
        readiness_item = _as_dict(readiness_by_interface.get(ifname))
        adapters.append(
            {
                "interface": ifname,
                "phy": phy,
                "bus_type": item.get("bus_type") or item.get("bus") or "unknown",
                "recommended": ifname == inventory_data.get("recommended"),
                "selected_for_report": ifname == report_adapter,
                "is_active_uplink": bool(
                    active_uplink_interface and ifname == active_uplink_interface
                ),
                "capabilities": {
                    "ap_mode": _optional_bool(
                        item.get("supports_ap", item.get("supports_ap_mode"))
                    ),
                    "supports_2ghz": _optional_bool(item.get("supports_2ghz")),
                    "supports_5ghz": _optional_bool(item.get("supports_5ghz")),
                    "supports_6ghz": _optional_bool(item.get("supports_6ghz")),
                    "supports_80mhz": _optional_bool(item.get("supports_80mhz")),
                    "supports_wifi6_he": _optional_bool(item.get("supports_wifi6")),
                    "supports_sta_ap_concurrency": concurrency.get(str(phy)) if phy else None,
                },
                "readiness": {
                    "state": readiness_item.get("readiness_state"),
                    "score": readiness_item.get("recommendation_score"),
                    "reason_codes": _as_list(readiness_item.get("reason_codes")),
                    "explanation": readiness_item.get("explanation"),
                },
            }
        )

    selected_adapter = next(
        (item for item in adapters if item.get("selected_for_report")),
        None,
    )
    selected_capabilities = (
        dict(selected_adapter.get("capabilities") or {})
        if selected_adapter
        else {
            "ap_mode": None,
            "supports_2ghz": None,
            "supports_5ghz": None,
            "supports_6ghz": None,
            "supports_80mhz": None,
            "supports_wifi6_he": None,
            "supports_sta_ap_concurrency": None,
        }
    )

    firewall_view = _firewall_view(firewall)
    hostapd = _binary_view(_as_dict(binaries).get("hostapd"))
    dnsmasq = _binary_view(_as_dict(binaries).get("dnsmasq"))
    existing = _as_dict(existing_preflight)
    failures = _normalize_probe_failures(probe_failures)

    issues: List[Dict[str, Any]] = []
    actions: List[Dict[str, str]] = []
    seen_issues = set()
    seen_actions = set()

    def add_issue(
        code: str,
        severity: str,
        *,
        message: Optional[str] = None,
        action: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        stable_code, encoded_context = _stable_issue(code)
        issue_context: Dict[str, Any] = dict(encoded_context)
        if isinstance(context, Mapping):
            issue_context.update(
                {key: value for key, value in context.items() if value is not None}
            )
        key = (stable_code, severity, repr(issue_context))
        if key not in seen_issues:
            seen_issues.add(key)
            issues.append(
                {
                    "code": stable_code,
                    "severity": severity,
                    "message": message
                    or _ISSUE_MESSAGES.get(stable_code)
                    or _fallback_issue_message(stable_code),
                    "context": issue_context,
                }
            )
        action_message = action or _ACTION_MESSAGES.get(stable_code)
        if action_message and stable_code not in seen_actions:
            seen_actions.add(stable_code)
            actions.append({"code": stable_code, "message": action_message})

    for failure in failures:
        add_issue(
            "probe_unavailable",
            "warning",
            context=failure,
        )

    inventory_error = inventory_data.get("error")
    if inventory_error:
        add_issue("adapter_inventory_unavailable", "blocked")
    if not adapters:
        add_issue("no_wifi_adapter", "blocked")
    else:
        ap_capable_interfaces = [
            str(item["interface"])
            for item in adapters
            if _as_dict(item.get("capabilities")).get("ap_mode") is True
        ]
        if not ap_capable_interfaces:
            add_issue(
                "no_ap_capable_adapter",
                "blocked",
                context={
                    "adapter_count": len(adapters),
                    "interfaces": [str(item["interface"]) for item in adapters],
                },
            )

    if configured_adapter and not selected_adapter:
        add_issue("configured_adapter_not_found", "blocked")
    elif selected_adapter:
        ap_mode = selected_capabilities.get("ap_mode")
        if ap_mode is False:
            add_issue("ap_mode_unavailable", "blocked")
        elif ap_mode is None:
            add_issue("ap_mode_unknown", "warning")

        configured_band = _normalize_band(cfg.get("band_preference"))
        if configured_band == "5ghz" and selected_capabilities.get("supports_5ghz") is False:
            add_issue("configured_5ghz_unavailable", "blocked")
        if configured_band == "6ghz" and selected_capabilities.get("supports_6ghz") is False:
            add_issue("configured_6ghz_unavailable", "blocked")

        configured_width = str(cfg.get("channel_width") or "auto").strip().lower()
        if (
            configured_band == "5ghz"
            and configured_width == "80"
            and selected_capabilities.get("supports_80mhz") is False
        ):
            severity = "warning" if bool(cfg.get("allow_fallback_40mhz")) else "blocked"
            add_issue("configured_80mhz_unavailable", severity)

    if not hostapd["available"]:
        add_issue("hostapd_not_available", "blocked")
    elif not hostapd.get("version"):
        add_issue("hostapd_version_unknown", "warning")
    if not dnsmasq["available"]:
        add_issue("dnsmasq_not_available", "blocked")
    elif not dnsmasq.get("version"):
        add_issue("dnsmasq_version_unknown", "warning")
    if _as_dict(binaries).get("selection_error"):
        add_issue("binary_selection_failed", "blocked")

    internet_required = bool(cfg.get("enable_internet", True)) and not bool(
        cfg.get("bridge_mode", False)
    )
    if internet_required and firewall_view["backend"] == "unknown":
        add_issue("firewall_backend_not_detected", "warning")
    if internet_required and not active_uplink_interface:
        add_issue("default_uplink_not_detected", "warning")
    if report_adapter and report_adapter == active_uplink_interface:
        add_issue(ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK, "blocked")
    if not os_family:
        add_issue("platform_family_unknown", "warning")

    existing_details = _as_dict(existing.get("details"))
    for code in _as_list(existing.get("errors")):
        stable_code, _encoded_context = _stable_issue(code)
        add_issue(
            str(code),
            "blocked",
            context=_preflight_issue_context(stable_code, existing_details),
        )
    for code in _as_list(existing.get("warnings")):
        stable_code, _encoded_context = _stable_issue(code)
        add_issue(
            str(code),
            "warning",
            context=_preflight_issue_context(stable_code, existing_details),
        )

    if any(item["severity"] == "blocked" for item in issues):
        overall_readiness = "blocked"
    elif issues:
        overall_readiness = "warning"
    else:
        overall_readiness = "ready"

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "overall_readiness": overall_readiness,
        "platform": {
            "os_id": os_data.get("id"),
            "os_name": os_data.get("pretty_name") or os_data.get("name"),
            "os_version": os_data.get("version_id"),
            "os_family": os_family or "unknown",
            "os_flavor": os_flavor,
            "package_manager_family": _package_manager_family(
                os_family if isinstance(os_family, str) else None,
                os_flavor,
                is_immutable,
            ),
            "host_kind": host_kind,
            "is_steamos": is_steamos,
            "is_mutable_linux": False if is_steamos or is_immutable else (True if os_known else None),
            "is_immutable": is_immutable,
            "immutability_signal": immutability.get("signal"),
        },
        "firewall": firewall_view,
        "services": {
            "network_manager": {
                "present": nm_present,
                "active": nm_active,
                "status": _service_status(present=nm_present, active=nm_active),
                "nmcli": bool(nm_data.get("nmcli") or platform_nm.get("nmcli")),
            },
            "iwd": {
                "present": iwd_present,
                "active": iwd_active,
                "status": _service_status(
                    present=iwd_present,
                    active=iwd_active,
                    reported=iwd_data.get("status"),
                ),
                "iwctl": bool(iwd_data.get("iwctl")),
            },
        },
        "binaries": {
            "hostapd": hostapd,
            "dnsmasq": dnsmasq,
            "selection_error": _as_dict(binaries).get("selection_error"),
        },
        "network": {
            "active_uplink_interface": active_uplink_interface,
        },
        "wifi": {
            "configured_adapter": configured_adapter,
            "recommended_adapter": inventory_data.get("recommended"),
            "basic_mode_recommended_adapter": readiness_data.get("basic_mode_recommended"),
            "selected_adapter": report_adapter,
            "selected_adapter_capabilities": selected_capabilities,
            "adapters": adapters,
        },
        "target_configuration": {
            "band": _normalize_band(cfg.get("band_preference")),
            "channel_width": str(cfg.get("channel_width") or "auto").strip().lower(),
            "allow_fallback_40mhz": bool(cfg.get("allow_fallback_40mhz")),
            "country": cfg.get("country"),
            "security": cfg.get("ap_security"),
            "internet_sharing": bool(cfg.get("enable_internet", True)),
            "bridge_mode": bool(cfg.get("bridge_mode", False)),
        },
        "evidence": {
            "stability": "debug",
            "probe_failures": failures,
            "raw_probe_results": {
                "platform_matrix": platform_data,
                "firewall": _as_dict(firewall),
                "existing_preflight": existing,
            },
        },
        "issues": issues,
        "recommended_actions": actions,
    }


def _binary_source(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    resolved_path = os.path.realpath(path)
    try:
        candidates = vendor_bin_dirs()
    except Exception:
        candidates = []
    for directory in candidates:
        try:
            if os.path.commonpath((resolved_path, os.path.realpath(directory))) == os.path.realpath(directory):
                return "bundled"
        except (OSError, ValueError):
            continue
    return "system"


def _version_from_output(name: str, output: str) -> Optional[str]:
    matcher = _HOSTAPD_VERSION_RE if name == "hostapd" else _DNSMASQ_VERSION_RE
    match = matcher.search(output or "")
    return match.group(1).strip() if match else None


def _empty_binary(path: Optional[str] = None) -> Dict[str, Any]:
    return {
        "available": bool(path),
        "source": _binary_source(path),
        "path": path,
        "version": None,
        "capabilities": {},
        "probe_error": None,
    }


def _collect_runtime_binaries() -> Dict[str, Any]:
    """Inspect binary paths and versions without invoking lifecycle setup."""

    inspection = _as_dict(supervisor.inspect_runtime_binaries())
    selected = {
        "hostapd": inspection.get("hostapd"),
        "dnsmasq": inspection.get("dnsmasq"),
    }
    selection_error = inspection.get("selection_error")
    probe_env = os.environ.copy()
    probe_env.update(
        {
            str(key): str(value)
            for key, value in _as_dict(inspection.get("probe_environment")).items()
        }
    )

    hostapd_path = selected.get("hostapd")
    hostapd_info = _empty_binary(
        hostapd_path if isinstance(hostapd_path, str) else None
    )
    if hostapd_info["available"]:
        caps = preflight.probe_hostapd_capabilities(hostapd_info["path"])
        hostapd_info["capabilities"] = {
            "sae": caps.get("sae"),
            "he": caps.get("he"),
        }
        hostapd_info["version"] = _version_from_output(
            "hostapd", str(caps.get("raw") or "")
        )
        hostapd_info["probe_error"] = caps.get("error")

    dnsmasq_path = selected.get("dnsmasq")
    dnsmasq_info = _empty_binary(
        dnsmasq_path if isinstance(dnsmasq_path, str) else None
    )
    if dnsmasq_info["available"]:
        result = host_probes.run_command(
            [dnsmasq_info["path"], "--version"],
            timeout_s=2.0,
            env=probe_env,
        )
        output = result.combined_output()
        dnsmasq_info["version"] = _version_from_output("dnsmasq", output)
        if not result.ok:
            dnsmasq_info["probe_error"] = (
                result.error or f"dnsmasq_version_failed(rc={result.exit_status})"
            )

    return {
        "hostapd": hostapd_info,
        "dnsmasq": dnsmasq_info,
        "selection_error": str(selection_error) if selection_error else None,
    }


def _safe_collect(
    name: str,
    callback: Callable[[], Any],
    fallback: Any,
    failures: List[Dict[str, str]],
) -> Any:
    try:
        return callback()
    except Exception as exc:
        failures.append(
            {
                "probe": name,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
        )
        return fallback


def collect_preflight_report(
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose the existing read-only probes into the canonical report."""

    cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
    if isinstance(config, Mapping):
        cfg.update(config)

    failures: List[Dict[str, str]] = []
    platform_matrix = _safe_collect(
        "platform",
        collect_platform_matrix,
        {},
        failures,
    )
    firewall = _safe_collect(
        "firewall",
        host_probes.probe_firewall_backends,
        {"selected_backend": "unknown", "rationale": "probe_failed"},
        failures,
    )
    network_manager = _safe_collect(
        "network_manager",
        host_probes.probe_network_manager,
        {"nmcli": False, "running": False},
        failures,
    )
    iwd = _safe_collect(
        "iwd",
        host_probes.probe_iwd,
        {"present": False, "active": False, "status": "unknown", "iwctl": False},
        failures,
    )
    binaries = _safe_collect(
        "runtime_binaries",
        _collect_runtime_binaries,
        {
            "hostapd": _empty_binary(),
            "dnsmasq": _empty_binary(),
            "selection_error": "probe_failed",
        },
        failures,
    )
    inventory = _safe_collect(
        "adapter_inventory",
        get_adapters,
        {"error": "probe_failed", "adapters": [], "recommended": None},
        failures,
    )
    readiness = _safe_collect(
        "adapter_readiness",
        lambda: build_readiness_model(_as_dict(inventory)),
        {"adapters": [], "recommended": None, "basic_mode_recommended": None},
        failures,
    )
    active_uplink = _safe_collect(
        "default_uplink",
        host_probes.probe_default_uplink,
        None,
        failures,
    )

    concurrency_by_phy: Dict[str, Optional[bool]] = {}
    for item in _as_list(_as_dict(inventory).get("adapters")):
        adapter = _as_dict(item)
        phy = adapter.get("phy")
        if not isinstance(phy, str) or not phy or phy in concurrency_by_phy:
            continue
        concurrency_by_phy[phy] = _safe_collect(
            f"sta_ap_concurrency:{phy}",
            lambda phy_name=phy: probe_ap_managed_concurrency(phy_name),
            None,
            failures,
        )

    report_adapter_name = _report_adapter_name(_as_dict(inventory), cfg)
    adapter = _find_adapter(_as_dict(inventory), report_adapter_name)
    hostapd_info = _as_dict(_as_dict(binaries).get("hostapd"))
    hostapd_capabilities = _as_dict(hostapd_info.get("capabilities"))
    if hostapd_info.get("probe_error"):
        hostapd_capabilities["error"] = hostapd_info.get("probe_error")
    existing_preflight = _safe_collect(
        "existing_preflight",
        lambda: preflight.run(
            cfg,
            adapter=adapter,
            band=_normalize_band(cfg.get("band_preference")),
            ap_security=str(cfg.get("ap_security") or "wpa2").strip().lower(),
            enable_internet=bool(cfg.get("enable_internet", True)),
            hostapd_capabilities=hostapd_capabilities,
        ),
        {"errors": [], "warnings": [], "details": {}},
        failures,
    )

    return build_preflight_report(
        platform_matrix=_as_dict(platform_matrix),
        firewall=_as_dict(firewall),
        network_manager=_as_dict(network_manager),
        iwd=_as_dict(iwd),
        binaries=_as_dict(binaries),
        inventory=_as_dict(inventory),
        readiness=_as_dict(readiness),
        active_uplink_interface=(
            str(active_uplink) if isinstance(active_uplink, str) and active_uplink else None
        ),
        concurrency_by_phy=concurrency_by_phy,
        existing_preflight=_as_dict(existing_preflight),
        config=cfg,
        probe_failures=failures,
    )
