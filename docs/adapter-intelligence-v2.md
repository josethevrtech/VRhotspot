# Adapter Intelligence v2 Design

Status: documentation-only design. This document describes a future adapter readiness and scoring model for VR Hotspot v1.1.0. It does not require runtime or test changes yet.

## Goals

- Explain which Wi-Fi adapter should be used for VR hotspot mode and why.
- Preserve full adapter inventory visibility for Advanced Mode and diagnostics.
- Give Basic Mode a strict, simple recommendation surface that avoids known weak choices.
- Make Wi-Fi 6E readiness explicit before a user spends time debugging 6 GHz failures.
- Keep the model structured enough to become a stable API response.

## Current Behavior

The current adapter inventory is built by `backend/vr_hotspotd/adapters/inventory.py`.

`get_adapters()` currently:

- Parses `iw dev` to map physical interfaces to PHY names.
- Skips virtual AP interfaces whose names match `x<digit>...`.
- Skips sysfs devices detected as virtual.
- Parses `iw phy <phy> info` for:
  - AP interface mode support.
  - Wi-Fi 6 support using `HE Iftypes` with AP first, then fallback markers such as `802.11ax` or `HE Capabilities`.
  - 80 MHz support using HE80 or VHT capability hints.
  - enabled 2.4 GHz, 5 GHz, and 6 GHz frequency ranges.
- Parses `iw reg get` for global and per-PHY regulatory country/source.
- Detects bus type from `/sys/class/net/<ifname>/device`, currently `usb`, `pci`, `virtual`, or `unknown`.
- Scores adapters with capability points:
  - AP mode: `+50`.
  - 6 GHz enabled frequencies: `+15`.
  - 5 GHz enabled frequencies: `+10`.
  - 80 MHz support: `+20`.
  - self-managed PHY regdomain: `+20`.
  - non-global/non-unknown regulatory country: `+10`.
  - `wlan0` deprioritization: `-30`.
- Picks `recommended` as the highest-scoring AP-capable adapter.
- Returns notes explaining that selection is capability-based and that 6 GHz is inferred from enabled `iw phy` frequencies.

The current `/v1/adapters` endpoint returns `get_adapters()` under the existing API envelope.

Current start behavior in `backend/vr_hotspotd/lifecycle.py` also affects adapter readiness:

- A configured `ap_adapter` is normalized, including virtual names such as `x0wlan1` back to their physical adapter when possible.
- If no adapter is configured and the band is 5 GHz, USB adapters with AP and 5 GHz support are preferred before falling back to the inventory recommendation.
- For 6 GHz starts, selection requires an AP-capable adapter with `supports_6ghz`.
- Basic Mode currently requires 5 GHz, requires an 80 MHz-capable adapter, and disables 40 MHz fallback.
- 5 GHz starts require 80 MHz support for VR readiness.
- Wi-Fi 6 startup flags are gated by `supports_wifi6`; requesting Wi-Fi 6 on an unsupported adapter records `wifi6_not_supported_on_adapter`.
- 6 GHz starts require WPA3-SAE.
- NetworkManager ownership is checked before start and remediation is attempted.

Relevant tests today cover:

- Adapter inventory parsing, Wi-Fi 6 markers, band support, no-IR filtering, virtual interface exclusion, and AP support.
- AP adapter normalization for stale and virtual interface names.
- Basic Mode 5 GHz/80 MHz enforcement and 40 MHz fallback blocking.
- Wi-Fi 6 flag gating.
- `iw` parsing for interface modes, AP/managed concurrency, 5 GHz 80 MHz channel candidates, DFS policy, and regulatory-domain errors.

## Proposed Readiness Model

Adapter Intelligence v2 should produce one readiness object per candidate interface plus a top-level recommendation. The model should keep raw-ish facts separate from derived recommendation fields so UI, tests, and future diagnostics can reason about the same data.

```json
{
  "interface": "wlan1",
  "driver": "mt7921u",
  "bus_type": "usb",
  "chipset_vendor_guess": {
    "vendor": "MediaTek",
    "chipset": "MT7921U",
    "source": "driver_usb_id"
  },
  "supports_ap_mode": true,
  "supports_2ghz": true,
  "supports_5ghz": true,
  "supports_6ghz": false,
  "regulatory_domain": {
    "status": "valid",
    "country": "US",
    "source": "global",
    "global_country": "US",
    "self_managed": false
  },
  "channel_width_hints": {
    "supports_20mhz": true,
    "supports_40mhz": true,
    "supports_80mhz": true,
    "supports_160mhz": false,
    "best_vr_width_mhz": 80,
    "evidence": ["vht80"]
  },
  "basic_mode_visibility": {
    "visible": true,
    "selectable": true,
    "rank": 1,
    "reason": "usb_5ghz_80mhz_ap"
  },
  "readiness_state": "good_for_vr",
  "six_ghz_state": "not_supported",
  "recommendation_score": 86,
  "reason_codes": [
    "supports_ap_mode",
    "usb_adapter",
    "supports_5ghz",
    "supports_80mhz",
    "regdom_valid"
  ],
  "explanation": "USB adapter wlan1 supports AP mode, 5 GHz, and 80 MHz channels, making it a good VR hotspot choice. It does not expose usable 6 GHz channels."
}
```

### Required Fields

- `interface`: Linux interface name, such as `wlan1` or `wlx...`.
- `driver`: Kernel driver/module when detectable from sysfs, ethtool, or `lspci`/`lsusb`; otherwise `unknown`.
- `bus_type`: `usb`, `pci`, `sdio`, `platform`, `virtual`, or `unknown`.
- `chipset_vendor_guess`: Best-effort vendor/chipset/model guess, with a source field. This must be explicitly treated as a guess unless sourced from a reliable ID table.
- `supports_ap_mode`: Boolean or `null` if `iw` data is unavailable.
- `supports_2ghz`: Boolean inferred from enabled 2.4 GHz frequencies.
- `supports_5ghz`: Boolean inferred from enabled 5 GHz frequencies.
- `supports_6ghz`: Boolean inferred from enabled 6 GHz frequencies, separate from overall 6 GHz readiness.
- `regulatory_domain`: Normalized country, source, self-managed flag, and status.
- `channel_width_hints`: Derived width support and best VR width from VHT/HE/EHT capability text and channel candidates.
- `basic_mode_visibility`: Whether Basic Mode should show/select this adapter.
- `readiness_state`: One of the proposed adapter readiness states.
- `six_ghz_state`: One of the proposed 6 GHz states.
- `recommendation_score`: Stable numeric score for sorting.
- `reason_codes`: Machine-readable reasons used by tests, UI badges, and logs.
- `explanation`: One concise human-readable sentence or paragraph.

## Readiness States

- `excellent_for_vr`: AP-capable, external/preferred adapter, 5 GHz or 6 GHz-ready, 80 MHz or better, valid regulatory domain, no known blocking warnings.
- `good_for_vr`: AP-capable, 5 GHz, 80 MHz-capable, valid enough regulatory context, but missing 6 GHz or carrying minor uncertainty.
- `usable_with_limitations`: AP-capable but constrained, such as 2.4 GHz only, 5 GHz without 80 MHz, DFS-only candidates, unknown driver details, or Basic Mode-hidden.
- `not_recommended`: Technically usable or detectable but a poor default, such as internal `wlan0`, missing 80 MHz for VR, global regulatory domain, known driver warnings, or NetworkManager risk.
- `unsupported`: Not usable for hotspot AP mode, missing from sysfs, virtual-only, no AP support, no usable channels, or required tooling failed.

## 6 GHz States

- `supported`: Adapter exposes AP-capable 6 GHz operation, regulatory domain allows usable initiating radiation, hostapd supports the needed 6 GHz/WPA3-SAE configuration, and platform checks do not block it.
- `blocked_by_regdomain`: Hardware/driver exposes 6 GHz frequencies but they are disabled, no-IR, global `00`, unknown, or otherwise unavailable due to regulatory state.
- `blocked_by_driver`: Chipset appears 6 GHz-capable, but AP mode, HE/EHT AP iftypes, or usable 6 GHz channel exposure is missing.
- `blocked_by_hostapd`: Kernel/driver/regdomain look acceptable, but hostapd build or config support is missing or incompatible.
- `unknown`: Required signals could not be collected or conflict.
- `not_supported`: No evidence of 6 GHz adapter support.

## Regulatory Domain Status

Suggested `regulatory_domain.status` values:

- `valid`: Effective country is a real two-letter country and relevant channels are usable.
- `global_or_unknown`: Country is `00`, `unknown`, missing, or unreadable.
- `self_managed`: PHY provides a self-managed regulatory domain; include the actual country when present.
- `no_ir_blocked`: Frequencies exist but initiating radiation is blocked.
- `dfs_only`: Usable candidates require DFS and DFS is disabled by policy.
- `conflicting`: Global and per-PHY information conflict in a way that changes channel availability.

## Scoring Proposal

The score should remain deterministic and explainable. A suggested 0-100 baseline:

- `+35` AP mode support.
- `+20` 5 GHz support.
- `+12` 6 GHz readiness when `six_ghz_state=supported`.
- `+18` 80 MHz support.
- `+5` 160 MHz support, capped so 160 MHz does not outweigh stable 80 MHz.
- `+8` USB/external adapter preference for VR hotspot use.
- `+6` valid regulatory domain.
- `+4` self-managed PHY regulatory data when it improves confidence.
- `-20` internal/default `wlan0` when another AP-capable adapter exists.
- `-25` no 80 MHz on 5 GHz.
- `-30` global/unknown regulatory domain when the requested band needs it.
- `-40` no AP mode.
- `-15` NetworkManager-managed or other current host constraint.

The implementation should clamp the final score to `0..100`. The score should not be the only source of truth; `readiness_state`, `six_ghz_state`, and `reason_codes` should explain any hard gates.

## Basic Mode Visibility

Basic Mode should use the same readiness model but expose a smaller decision:

- Show and auto-select adapters that are AP-capable, support 5 GHz, support 80 MHz, and are not clearly blocked by regulatory state.
- Prefer USB adapters over internal adapters.
- Hide or deprioritize internal `wlan0` when a better external adapter exists.
- Hide unsupported adapters from the default Basic Mode selector, but preserve them in Advanced Mode and diagnostics.
- Never select 2.4 GHz-only adapters in Basic Mode.
- Do not expose 40 MHz fallback as a Basic Mode choice.

Suggested `basic_mode_visibility.reason` examples:

- `usb_5ghz_80mhz_ap`
- `internal_deprioritized`
- `missing_80mhz`
- `missing_5ghz`
- `missing_ap_mode`
- `regdomain_blocks_required_band`

## Reason Codes

Reason codes should be stable, lowercase strings. Proposed codes:

- `supports_ap_mode`
- `missing_ap_mode`
- `usb_adapter`
- `pci_or_internal_adapter`
- `wlan0_deprioritized`
- `supports_2ghz`
- `supports_5ghz`
- `supports_6ghz`
- `supports_80mhz`
- `missing_80mhz`
- `regdom_valid`
- `regdom_global_or_unknown`
- `regdom_no_ir_blocks_6ghz`
- `dfs_required_but_disabled`
- `wifi6_supported`
- `wifi6_not_supported_on_adapter`
- `hostapd_6ghz_not_available`
- `networkmanager_managed`
- `basic_mode_visible`
- `basic_mode_hidden`
- `no_adapter_found`

## Example Outputs

### Good USB 5 GHz Adapter

```json
{
  "recommended": "wlan1",
  "adapters": [
    {
      "interface": "wlan1",
      "driver": "mt7921u",
      "bus_type": "usb",
      "chipset_vendor_guess": {
        "vendor": "MediaTek",
        "chipset": "MT7921U",
        "source": "driver"
      },
      "supports_ap_mode": true,
      "supports_2ghz": true,
      "supports_5ghz": true,
      "supports_6ghz": false,
      "regulatory_domain": {
        "status": "valid",
        "country": "US",
        "source": "global",
        "global_country": "US",
        "self_managed": false
      },
      "channel_width_hints": {
        "supports_20mhz": true,
        "supports_40mhz": true,
        "supports_80mhz": true,
        "supports_160mhz": false,
        "best_vr_width_mhz": 80,
        "evidence": ["vht80"]
      },
      "basic_mode_visibility": {
        "visible": true,
        "selectable": true,
        "rank": 1,
        "reason": "usb_5ghz_80mhz_ap"
      },
      "readiness_state": "good_for_vr",
      "six_ghz_state": "not_supported",
      "recommendation_score": 86,
      "reason_codes": [
        "supports_ap_mode",
        "usb_adapter",
        "supports_5ghz",
        "supports_80mhz",
        "regdom_valid",
        "basic_mode_visible"
      ],
      "explanation": "wlan1 is the recommended adapter because it is a USB AP-capable adapter with usable 5 GHz and 80 MHz support."
    }
  ]
}
```

### Wi-Fi 6E Adapter Where 6 GHz Is Blocked

```json
{
  "recommended": "wlan2",
  "adapters": [
    {
      "interface": "wlan2",
      "driver": "mt7921u",
      "bus_type": "usb",
      "chipset_vendor_guess": {
        "vendor": "MediaTek",
        "chipset": "MT7921U/MT7922 family",
        "source": "driver"
      },
      "supports_ap_mode": true,
      "supports_2ghz": true,
      "supports_5ghz": true,
      "supports_6ghz": true,
      "regulatory_domain": {
        "status": "no_ir_blocked",
        "country": "00",
        "source": "global",
        "global_country": "00",
        "self_managed": false
      },
      "channel_width_hints": {
        "supports_20mhz": true,
        "supports_40mhz": true,
        "supports_80mhz": true,
        "supports_160mhz": true,
        "best_vr_width_mhz": 80,
        "evidence": ["he80", "he160", "6ghz_frequencies_present"]
      },
      "basic_mode_visibility": {
        "visible": true,
        "selectable": true,
        "rank": 1,
        "reason": "usb_5ghz_80mhz_ap"
      },
      "readiness_state": "good_for_vr",
      "six_ghz_state": "blocked_by_regdomain",
      "recommendation_score": 82,
      "reason_codes": [
        "supports_ap_mode",
        "usb_adapter",
        "supports_5ghz",
        "supports_6ghz",
        "supports_80mhz",
        "regdom_global_or_unknown",
        "regdom_no_ir_blocks_6ghz"
      ],
      "explanation": "wlan2 is a strong 5 GHz VR adapter, but 6 GHz is blocked because the effective regulatory domain is global or no-IR."
    }
  ]
}
```

### Internal wlan0 Adapter Deprioritized in Basic Mode

```json
{
  "recommended": "wlan1",
  "adapters": [
    {
      "interface": "wlan0",
      "driver": "iwlwifi",
      "bus_type": "pci",
      "chipset_vendor_guess": {
        "vendor": "Intel",
        "chipset": "AX200/AX210 family",
        "source": "driver"
      },
      "supports_ap_mode": true,
      "supports_2ghz": true,
      "supports_5ghz": true,
      "supports_6ghz": false,
      "regulatory_domain": {
        "status": "valid",
        "country": "US",
        "source": "kernel-managed",
        "global_country": "US",
        "self_managed": false
      },
      "channel_width_hints": {
        "supports_20mhz": true,
        "supports_40mhz": true,
        "supports_80mhz": true,
        "supports_160mhz": false,
        "best_vr_width_mhz": 80,
        "evidence": ["vht80", "he80"]
      },
      "basic_mode_visibility": {
        "visible": false,
        "selectable": false,
        "rank": 99,
        "reason": "internal_deprioritized"
      },
      "readiness_state": "not_recommended",
      "six_ghz_state": "not_supported",
      "recommendation_score": 58,
      "reason_codes": [
        "supports_ap_mode",
        "pci_or_internal_adapter",
        "wlan0_deprioritized",
        "supports_5ghz",
        "supports_80mhz",
        "basic_mode_hidden"
      ],
      "explanation": "wlan0 can advertise AP mode and 5 GHz, but it is the internal adapter and is deprioritized for Basic Mode when an external AP-capable adapter is available."
    }
  ]
}
```

### No Adapter Found

```json
{
  "recommended": null,
  "adapters": [],
  "global_regulatory_domain": {
    "status": "unknown",
    "country": "unknown",
    "source": "unavailable"
  },
  "summary": {
    "readiness_state": "unsupported",
    "six_ghz_state": "unknown",
    "recommendation_score": 0,
    "reason_codes": ["no_adapter_found"],
    "explanation": "No physical Wi-Fi adapter was found. Connect a USB Wi-Fi adapter that supports AP mode, 5 GHz, and 80 MHz channels."
  }
}
```

## Future Endpoint

Adapter Intelligence v2 can become:

```text
GET /v1/adapters/readiness
```

The endpoint should use the existing authenticated API envelope and should not replace `/v1/adapters` immediately. A future response shape:

```json
{
  "recommended": "wlan1",
  "basic_mode_recommended": "wlan1",
  "adapters": [],
  "global_regulatory_domain": {
    "status": "valid",
    "country": "US",
    "raw": "country US: DFS-FCC"
  },
  "host_capabilities": {
    "iw_available": true,
    "hostapd_available": true,
    "hostapd_6ghz_capable": null,
    "networkmanager_running": true
  },
  "notes": [
    "Readiness is based on current driver, regulatory, hostapd, and platform signals.",
    "Advanced Mode should continue to expose non-recommended adapters."
  ]
}
```

Implementation path:

1. Add a pure normalization/scoring module that accepts inventory, `iw` text, regdomain data, hostapd capability probes, and current config.
2. Add focused unit tests for state mapping, score ordering, reason codes, and no-adapter behavior.
3. Wire `/v1/adapters/readiness` as read-only.
4. Update Basic Mode UI to consume `basic_mode_visibility` and explanations.
5. Keep `/v1/adapters` stable until the UI and tests have migrated.

## Non-Goals for This Design Step

- No runtime code changes.
- No test changes.
- No change to existing `/v1/adapters` response.
- No adapter blocklist unless it is backed by local evidence and tests.
- No claim that a chipset guess is authoritative without a reliable source.
