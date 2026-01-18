from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from vr_hotspotd import os_release


_KNOWN_PROFILES = ("bazzite", "steamos", "cachyos", "arch", "fedora")
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def vendor_strict_enabled() -> bool:
    return _env_flag("VR_HOTSPOT_VENDOR_STRICT")


def vendor_force_enabled() -> bool:
    return _env_flag("VR_HOTSPOT_FORCE_VENDOR_BIN")


def _split_tokens(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.replace(",", " ").split() if item.strip()]


def vendor_profiles(info: Optional[Dict[str, str]] = None) -> List[str]:
    env_profile = os.environ.get("VR_HOTSPOT_VENDOR_PROFILE", "")
    if env_profile:
        return _split_tokens(env_profile)

    info = info or os_release.read_os_release()
    tokens: List[str] = []
    for key in ("id", "variant_id", "id_like"):
        tokens.extend(_split_tokens(info.get(key)))

    profiles: List[str] = []
    for token in tokens:
        if token in _KNOWN_PROFILES and token not in profiles:
            profiles.append(token)
    return profiles


def _vendor_root() -> Path:
    install_dir = os.environ.get("VR_HOTSPOT_INSTALL_DIR")
    if install_dir:
        cand = Path(install_dir) / "backend" / "vendor"
        if cand.is_dir():
            return cand

    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "vendor",
        Path("/var/lib/vr-hotspot/app/backend/vendor"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def vendor_bin_dirs(info: Optional[Dict[str, str]] = None) -> List[Path]:
    root = _vendor_root()
    dirs: List[Path] = []
    for profile in vendor_profiles(info):
        cand = root / "bin" / profile
        if cand.is_dir():
            dirs.append(cand)
    base = root / "bin"
    if base.is_dir():
        dirs.append(base)
    if not dirs:
        dirs.append(base)
    return dirs


def vendor_lib_dirs(
    info: Optional[Dict[str, str]] = None,
    *,
    preferred_profile: Optional[str] = None,
) -> List[Path]:
    root = _vendor_root()
    dirs: List[Path] = []
    if preferred_profile:
        cand = root / "lib" / preferred_profile
        if cand.is_dir():
            dirs.append(cand)
    base = root / "lib"
    if base.is_dir() and base not in dirs:
        dirs.append(base)
    if not dirs:
        dirs.append(base)
    return dirs


def resolve_vendor_exe(
    name: str,
    info: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[Path], Optional[str]]:
    root = _vendor_root()
    for profile in vendor_profiles(info):
        bin_dir = root / "bin" / profile
        exe = bin_dir / name
        if exe.is_file() and os.access(exe, os.X_OK):
            lib_dir = root / "lib" / profile
            if lib_dir.is_dir():
                return str(exe), lib_dir, profile
            base_lib = root / "lib"
            return str(exe), base_lib if base_lib.is_dir() else None, profile

    bin_dir = root / "bin"
    exe = bin_dir / name
    if exe.is_file() and os.access(exe, os.X_OK):
        base_lib = root / "lib"
        return str(exe), base_lib if base_lib.is_dir() else None, None

    return None, None, None


def resolve_vendor_required(
    names: List[str],
    info: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], Optional[Path], Optional[str], List[str]]:
    resolved: Dict[str, str] = {}
    missing: List[str] = []
    used_profile: Optional[str] = None

    strict = vendor_strict_enabled() or vendor_force_enabled()
    root = _vendor_root()
    base_bin = root / "bin"
    base_lib = root / "lib"

    profiles = vendor_profiles(info)
    profile = next((p for p in profiles if (base_bin / p).is_dir()), None)
    profile_bin = base_bin / profile if profile else None

    def _add_if_exe(path: Path, name: str) -> bool:
        if path.is_file() and os.access(path, os.X_OK):
            resolved[name] = str(path)
            return True
        return False

    for name in names:
        if profile_bin:
            if _add_if_exe(profile_bin / name, name):
                used_profile = profile
                continue
        if _add_if_exe(base_bin / name, name):
            continue
        if strict:
            missing.append(name)

    lib_dir = None
    if used_profile:
        cand = root / "lib" / used_profile
        if cand.is_dir():
            lib_dir = cand
        elif base_lib.is_dir():
            lib_dir = base_lib
    elif base_lib.is_dir() and resolved:
        lib_dir = base_lib

    if not strict:
        return resolved, lib_dir, used_profile, []

    return resolved, lib_dir, used_profile, missing
