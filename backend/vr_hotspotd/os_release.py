from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_OS_RELEASE_PATHS = ("/etc/os-release", "/usr/lib/os-release")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_os_release(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = _strip_quotes(value)
        if key:
            data[key] = value
    return data


def read_os_release(paths: Optional[Tuple[str, ...]] = None) -> Dict[str, str]:
    for path in paths or _OS_RELEASE_PATHS:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception:
            continue
        data = _parse_os_release(text)
        if data:
            return data
    return {}


def _split_like(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.replace(",", " ").split() if item.strip()]


def is_bazzite(info: Optional[Dict[str, str]] = None) -> bool:
    info = info or read_os_release()
    if not info:
        return False
    tokens: List[str] = []
    for key in ("id", "id_like", "variant_id", "variant", "name"):
        tokens.extend(_split_like(info.get(key)))
    return "bazzite" in tokens


def is_cachyos(info: Optional[Dict[str, str]] = None) -> bool:
    info = info or read_os_release()
    if not info:
        return False
    tokens: List[str] = []
    for key in ("id", "id_like", "variant_id", "variant", "name"):
        tokens.extend(_split_like(info.get(key)))
    return "cachyos" in tokens


def apply_platform_overrides(
    cfg: Dict[str, Any],
    info: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Apply OS-specific compatibility tweaks without mutating the input dict.
    """
    info = info or read_os_release()
    warnings: List[str] = []
    overrides: Dict[str, Any] = {}

    if is_bazzite(info):
        if cfg.get("optimized_no_virt", False):
            warnings.append("platform_bazzite_no_virt_may_fail")
        else:
            warnings.append("platform_bazzite_prefer_virt")

    # CachyOS: some adapters take longer to report AP-ready on first start.
    # If timeout is at/below default, bump it to reduce false timeouts.
    if is_cachyos(info):
        try:
            timeout_s = float(cfg.get("ap_ready_timeout_s", 0.0))
        except Exception:
            timeout_s = 0.0
        if timeout_s <= 6.0:
            overrides["ap_ready_timeout_s"] = 12.0
            warnings.append("platform_cachyos_increased_ap_ready_timeout")

    if not overrides:
        return cfg, warnings
    updated = dict(cfg)
    updated.update(overrides)
    return updated, warnings
