# Implementation Summary: Hostapd Country Code Validation

## Status: ✅ COMPLETE

All requested functionality has been successfully implemented in `backend/vr_hotspotd/lifecycle.py`.

---

## What Was Requested

1. **ensure_hostapd_ctrl_interface_dir(conf_path: str)** - Parse and create ctrl_interface directory
2. **validate_hostapd_country(conf_path: str)** - Validate country_code when ieee80211d=1
3. **enforce_hostapd_country(conf_path: str, resolved_country: str)** - Enforce valid country_code
4. Wire these into the start flow after config dir discovery
5. Add fallback AP readiness check (hostapd PID + iface UP + type AP)

---

## Implementation Details

### 1. Three New Functions Added (Lines 123-251)

#### `ensure_hostapd_ctrl_interface_dir(conf_path: str)` (Line 123)
- Parses `ctrl_interface=VALUE` from hostapd.conf
- Handles both plain path and `DIR=/path` formats
- Creates directory with `mkdir -p` and `chmod 0o755`
- Logs success/failure with structured fields

#### `validate_hostapd_country(conf_path: str)` (Line 163)
- Parses `ieee80211d` and `country_code` from hostapd.conf
- Returns error code `"hostapd_invalid_country_code_for_80211d"` if:
  - ieee80211d=1 AND country_code is missing
  - ieee80211d=1 AND country_code="00"
  - ieee80211d=1 AND country_code doesn't match `/^[A-Z]{2}$/`
- Returns `None` if valid

#### `enforce_hostapd_country(conf_path: str, resolved_country: str)` (Line 204)
- Validates resolved_country matches `/^[A-Z]{2}$/` and != "00"
- Replaces existing `country_code=` line if different
- Appends `country_code=` line if missing
- Returns `True` if modified, `False` otherwise
- Logs updates with structured fields
- Never exposes secrets

### 2. Integration with Existing Code

#### Already Wired in `AttemptCapture._set_config_dir()` (Line 508-519)
```python
def _set_config_dir(self, conf_dir: Path) -> None:
    updated = False
    with self._lock:
        if self._config_dir != conf_dir:
            self._config_dir = conf_dir
            updated = True
    if updated:
        log.info("lnxrouter_config_dir_discovered", extra={"config_dir": str(conf_dir)})
        hostapd_conf = conf_dir / "hostapd.conf"
        if hostapd_conf.exists():
            ensure_hostapd_ctrl_interface_dir(str(hostapd_conf))  # ✅ Called here
        self._publish_state_if_changed()
```

#### Already Wired in `_check_hostapd_conf_country()` (Line 1754-1788)
This function is called 3 times in `start_hotspot()`:
- Line 3089: After primary engine start
- Line 3253: After no-virt fallback
- Line 3485: After fallback to 2.4GHz

```python
def _check_hostapd_conf_country(
    ap_ifname: Optional[str],
    *,
    capture: Optional["AttemptCapture"],
    attempt: str,
    resolved_country: Optional[str],
    timeout_s: float = 2.0,
) -> Optional[str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        conf_dir = _find_latest_conf_dir_any(ap_ifname, capture)
        if conf_dir:
            hostapd_conf = conf_dir / "hostapd.conf"
            if hostapd_conf.exists():
                conf_path = str(hostapd_conf)
                ensure_hostapd_ctrl_interface_dir(conf_path)          # ✅ Called
                if resolved_country:
                    enforce_hostapd_country(conf_path, resolved_country)  # ✅ Called
                err = validate_hostapd_country(conf_path)              # ✅ Called
                if err:
                    snippet = _hostapd_conf_sanitized_snippet(conf_path)
                    if snippet:
                        _write_capture_debug(
                            capture,
                            "hostapd_conf_sanitized.log",
                            snippet,
                        )
                    return err
                return None
        time.sleep(0.1)
    return None
```

#### Helper Function `_hostapd_conf_sanitized_snippet()` (Line 1717-1740)
Extracts safe config lines for capture (never exposes secrets):
- country_code
- ieee80211d
- ctrl_interface
- channel
- hw_mode
- wpa
- wpa_key_mgmt
- rsn_pairwise

### 3. Fallback AP Readiness Check

Already implemented in `_wait_for_ap_ready()` (Lines 1331-1334):
```python
# Fallback check
pids = _find_all_hostapd_pids()
if not pids:
    time.sleep(poll_s)
    continue

if not _is_iface_up(ap.ifname):
    time.sleep(poll_s)
    continue

info = _iw_dev_info_text(ap.ifname)
if "type AP" not in info:
    time.sleep(poll_s)
    continue

# Check SSID matches if provided
ssid_ok = False
if ssid and ssid.strip():
    if f"ssid {ssid}" in info:
        ssid_ok = True
    else:
        for line in info.splitlines():
            if line.strip() == f"ssid {ssid}":
                ssid_ok = True
                break
else:
    ssid_ok = True

if ssid_ok:
    log.info(
        "ap_ready_fallback_check_passed",
        extra={"ap_interface": ap.ifname, "hostapd_pids": pids, "ssid": ssid or None},
    )
    return ap
```

---

## Error Handling Flow

When country validation fails:
1. `validate_hostapd_country()` returns error code
2. Sanitized hostapd.conf snippet is captured to `hostapd_conf_sanitized.log`
3. Error code is returned from `_check_hostapd_conf_country()`
4. In `start_hotspot()`, if error is returned:
   - Engine is stopped
   - State is updated with error
   - `LifecycleResult` returned with error code
   - Start fails immediately

---

## Testing Recommendations

### Test Case 1: Valid Country Code
- Set `country=US` in config
- Set `ieee80211d=1` in hostapd options
- Expected: Start succeeds, country_code enforced in hostapd.conf

### Test Case 2: Missing Country Code with ieee80211d=1
- Set `ieee80211d=1` but no country_code
- Expected: Start fails with `hostapd_invalid_country_code_for_80211d`

### Test Case 3: Invalid Country Code "00"
- Set `country=00` and `ieee80211d=1`
- Expected: Country not enforced (00 is skipped), validation may fail

### Test Case 4: ctrl_interface Directory
- Check that `/run/hostapd` or specified directory is created
- Expected: Directory exists with 0755 permissions

### Test Case 5: Fallback Readiness
- Simulate hostapd_cli ping failure
- Expected: Fallback check uses `pgrep hostapd` + `iw dev` + interface UP status

---

## Files Changed

### `backend/vr_hotspotd/lifecycle.py`
- **Added**: Lines 123-251 (3 new functions)
- **Modified**: Line 518 (already calling `ensure_hostapd_ctrl_interface_dir`)
- **Existing**: Lines 1717-1788 (integration functions already present)
- **Existing**: Lines 1305-1370 (fallback check already present)

**Total new lines**: ~130 lines of new code
**Integration**: Fully wired into existing flow

---

## Conclusion

✅ All requested functionality is **fully implemented and integrated**.

The implementation follows the exact specifications:
- Directory creation with proper permissions
- Country code validation with ieee80211d checking
- Country code enforcement for valid values
- Sanitized config capture (no secrets)
- Fallback AP readiness detection
- Structured logging throughout
- Error handling with immediate stop on validation failure

**No additional changes needed.**
